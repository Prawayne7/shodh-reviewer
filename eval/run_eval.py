"""Run deterministic offline evaluation against frozen labels (labels never enter reviewer input)."""
from __future__ import annotations
import argparse, hashlib, json, time, subprocess, sys
from pathlib import Path
import yaml
from oracle.interpreters import load_raw, runners
from oracle.policy import support_v1
from oracle.prospective import verify
from reviewer.loader import load_bundle
from reviewer.semantics import review_bundle
from reviewer.model_review import FakeModelClient, run_model_review

ROOT = Path(__file__).resolve().parents[1]

def sha(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in path.rglob('*') if x.is_file()):
        h.update(p.relative_to(path).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()

def load_labels():
    return {p.stem: yaml.safe_load(p.read_text()) for p in sorted((ROOT/'labels').glob('b*.yaml'))}

def metrics(rows):
    kinds = ('valid','defective','incomplete')
    out = {'total': len(rows), 'correct': sum(r['actual_status']==r['expected_status'] for r in rows),
           'accuracy': round(sum(r['actual_status']==r['expected_status'] for r in rows)/len(rows), 4) if rows else None,
           'by_case_type': {}}
    for k in kinds:
        group=[r for r in rows if r['case_type']==k]
        out['by_case_type'][k]={'count':len(group),'correct':sum(r['actual_status']==r['expected_status'] for r in group),
            'expected_status': group[0]['expected_status'] if group else None,
            'actual_status_counts': {s:sum(r['actual_status']==s for r in group) for s in ('PASS','CHANGES_REQUIRED','INCOMPLETE')}}
    out['false_passes']=[r['bundle'] for r in rows if r['case_type']=='defective' and r['actual_status']=='PASS']
    out['false_blocks']=[r['bundle'] for r in rows if r['case_type']=='valid' and r['actual_status']=='CHANGES_REQUIRED']
    out['incomplete_cases']=[r['bundle'] for r in rows if r['actual_status']=='INCOMPLETE']
    return out

def finding_quality(label: dict, report, artifact_runners) -> dict:
    """Compare emitted findings with frozen oracle expectations and disclose gaps."""
    expected = label.get('expected_findings', [])
    actual = [f.model_dump(mode='json') for f in report.findings if f.source == 'deterministic']
    matched_actual: set[int] = set(); matched_expected: set[int] = set(); details = []
    for ei, exp in enumerate(expected):
        exp_artifact = exp.get('artifact', '')
        exp_locations = set(exp.get('locations', []))
        exp_ce = exp.get('counterexample') or {}
        for ai, act in enumerate(actual):
            if ai in matched_actual:
                continue
            aa = act.get('artifact', '')
            artifact_ok = aa == exp_artifact or (exp_artifact == 'code' and aa.startswith('code:'))
            actual_loc = f"{aa}@{act.get('version')}#{act.get('location')}"
            location_ok = not exp_locations or any(
                actual_loc == loc or loc.endswith('#') and actual_loc.startswith(loc)
                or actual_loc.startswith((loc + '.', loc + ':'))
                for loc in exp_locations
            )
            ce = act.get('counterexample') or {}
            if exp_ce:
                run_artifact = artifact_runners.get(exp["oracle_artifact"])
                ticket = ce.get("ticket")
                independently_supported = (
                    ticket is not None and run_artifact is not None
                    and support_v1(ticket, label["intended_policy"]["wait_threshold_minutes"]) == ce.get("expected_route")
                    and run_artifact(ticket) == ce.get("actual_route")
                )
                ce_ok = (independently_supported and ce.get('expected_route') == exp_ce.get('expected_route') and
                         ce.get('actual_route') == exp_ce.get('actual_route') and
                         ce.get('expected_route') != ce.get('actual_route'))
            else:
                ce_ok = act.get('kind') in exp.get('acceptable_kinds', [exp.get('kind')]) and act.get('severity') == exp.get('severity')
            if artifact_ok and location_ok and ce_ok:
                matched_expected.add(ei); matched_actual.add(ai)
                refs = set(exp.get('rule_refs', []))
                details.append({'expected_finding_index': ei, 'actual_finding_id': act.get('id'),
                    'artifact_match': True, 'evidence_location_match': True,
                    'counterexample_or_issue_match': True, 'counterexample_verified': bool(exp_ce),
                    'rule_reference_match': not refs or act.get('rule_ref') in refs,
                    'expected_rule_refs': sorted(refs), 'actual_rule_ref': act.get('rule_ref')})
                break
    en, an = len(expected), len(actual)
    return {'expected_findings': en, 'actual_deterministic_findings': an,
        'matched_expected_findings': len(matched_expected),
        'finding_recall': round(len(matched_expected)/en, 4) if en else (1.0 if an == 0 else 0.0),
        'unsupported_findings': an-len(matched_actual),
        'finding_precision': round(len(matched_actual)/an, 4) if an else (1.0 if en == 0 else 0.0),
        'rule_reference_mismatches': sum(not d['rule_reference_match'] for d in details),
        'matches': details}

def run(client_mode='fake'):
    verify()
    labels=load_labels(); rows=[]; ablation_rows=[]; started=time.perf_counter(); calls=0; retries=0
    quality_rows=[]
    ablation_quality_rows=[]
    ablation_seconds = final_seconds = 0.0
    checkpoint_started = time.perf_counter()
    # Run the preserved Step 6 implementation in an isolated process against the
    # exact same fixture directories. This is a real source checkpoint, not a
    # pre-model status captured from the Step 9 process.
    checkpoint_raw = subprocess.check_output([sys.executable, str(ROOT/'checkpoint/step6/run_checkpoint.py'), str(ROOT)], text=True)
    checkpoint_seconds = time.perf_counter() - checkpoint_started
    checkpoint_rows = json.loads(checkpoint_raw)
    for bid, lab in labels.items():
        bundle=load_bundle(ROOT/'fixtures'/'bundles'/bid)
        run_started = time.perf_counter()
        ablation_report=review_bundle(bundle)
        ablation_seconds += time.perf_counter() - run_started
        run_started = time.perf_counter()
        report=review_bundle(bundle)
        ablation_status=ablation_report.status
        if client_mode=='fake':
            script=lab.get('model_script')
            if script:
                raw=(ROOT/script).read_text()
                scripted=json.loads(raw)
                if scripted.get('behavior') == 'timeout':
                    raw='__TIMEOUT__'
                elif scripted.get('behavior') == 'invented_reference':
                    raw=json.dumps({'observations':[{'rule_ref':'INVENTED_RULE','artifact':'requirements','version':'v0','location':'NOWHERE','evidence':'fabricated','summary':'unsupported claim','correction':'remove claim'}]})
            else: raw='{"observations":[]}'
            client=FakeModelClient([raw, raw] if raw == '__TIMEOUT__' or 'INVENTED_RULE' in raw else [raw])
            run_model_review(bundle, report, client)
            calls += client.calls
            retries += max(0, client.calls-1)
        ablation_rows.append({'bundle':bid,'split':lab['split'],'case_type':lab['case_type'],
            'expected_status':lab['expected_status'],'actual_status':ablation_status})
        final_seconds += time.perf_counter() - run_started
        artifact_runners = runners(load_raw(ROOT/'fixtures'/'bundles'/bid))
        q = finding_quality(lab, report, artifact_runners)
        ablation_quality_rows.append({"bundle": bid, **finding_quality(lab, ablation_report, artifact_runners)})
        quality_rows.append({'bundle': bid, 'split': lab['split'], 'case_type': lab['case_type'], **q})
        rows.append({'bundle':bid,'split':lab['split'],'case_type':lab['case_type'],
                     'expected_status':lab['expected_status'],'actual_status':report.status,
                     'finding_count':len(report.findings),'coverage_count':len(report.coverage.entries),
                     'model_outcomes':[x.outcome for x in report.provenance.model_calls]})
    elapsed=round(time.perf_counter()-started,4)
    q_expected = sum(x['expected_findings'] for x in quality_rows)
    q_matched = sum(x['matched_expected_findings'] for x in quality_rows)
    q_actual = sum(x['actual_deterministic_findings'] for x in quality_rows)
    q_unsupported = sum(x['unsupported_findings'] for x in quality_rows)
    return {'schema_version':'step9-evaluation-v2','client':client_mode,'rows':rows,
            'finding_quality': {'cases': quality_rows, 'expected_findings': q_expected,
                'actual_deterministic_findings': q_actual, 'matched_expected_findings': q_matched,
                'finding_recall': round(q_matched/q_expected,4) if q_expected else None,
                'unsupported_findings': q_unsupported,
                'rule_reference_mismatches': sum(x['rule_reference_mismatches'] for x in quality_rows),
                'finding_precision': round((q_actual-q_unsupported)/q_actual,4) if q_actual else None,
                 'supported_counterexamples': sum(d['counterexample_verified'] for x in quality_rows for d in x['matches']),
                'expected_counterexamples': sum(bool(f.get('counterexample')) for lab in labels.values() for f in lab.get('expected_findings', [])),
                'correct_rule_references': sum(d['rule_reference_match'] for x in quality_rows for d in x['matches']),
                'matching_policy': 'One-to-one artifact and primary location, with routes independently replayed for emitted tickets. Rule references scored separately.' },
            'overall':metrics(rows),'checkpoint_step6_preserved_implementation':metrics(checkpoint_rows),
            'ablation_model_layer_disabled_on_step9':metrics(ablation_rows),
            'development':metrics([r for r in rows if r['split']=='dev']),
            'heldout':metrics([r for r in rows if r['split']=='heldout']),
             'checkpoint_rows': checkpoint_rows,
            'ablation_rows': ablation_rows,
            'ablation_finding_quality': ablation_quality_rows,
            'resources':{'wall_seconds':elapsed,
                         'checkpoint': {'wall_seconds': round(checkpoint_seconds, 4), 'model_calls': 0, 'retries': 0},
                         'ablation': {'review_wall_seconds': round(ablation_seconds, 4), 'model_calls': 0, 'retries': 0},
                         'final_review_wall_seconds': round(final_seconds, 4),'model_calls':calls,'retries':retries,
                         'model_results_are_synthetic':client_mode=='fake'},
            'provenance':{'label_files_are_scoring_only':True,'checkpoint_source':'checkpoint/step6/reviewer (copied from the supplied Step 6 updated ZIP)',
                'checkpoint_runner':'checkpoint/step6/run_checkpoint.py','ablation':'same Step 9 deterministic implementation and inputs with model layer disabled; expected to match deterministic checkpoint only if deterministic code is unchanged',
                'bundle_hashes':{b:sha(ROOT/'fixtures'/'bundles'/b) for b in labels}}}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--client',default='fake',choices=['fake']); p.add_argument('--out',default='reports/final.json'); a=p.parse_args()
    result=run(a.client); out=ROOT/a.out; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'overall':result['overall'],'heldout':result['heldout'],'resources':result['resources']},indent=2))
    if result['overall']['accuracy'] != 1.0:
        raise SystemExit('Evaluation failed: status mismatch detected')
if __name__=='__main__': main()
