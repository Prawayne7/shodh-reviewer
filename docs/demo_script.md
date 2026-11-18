# Local demo

Run `python -X utf8 demo_local.py` from the project root after installing pinned dependencies. The script invokes the normal CLI, validates each JSON report and exits unsuccessfully if a demo verdict differs from the expected result. The JSON reports are saved under reports/demo.

For a short recording, show the three CLI runs in sequence. b01 passes, b14 identifies the threshold at S3 and shows the 30-minute route disagreement, and b19 abstains after two scripted fake-client timeouts. Close with the independent label check and the development versus designated held-out counts. The real terminal transcript is in reports/local-demo.txt.
