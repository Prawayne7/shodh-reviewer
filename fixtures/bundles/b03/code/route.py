def route(ticket):
    category = ticket.get("category")
    if not category:
        return "manual_review"
    if ticket["urgent"] and category not in ("spam",):
        return "priority_queue"
    hours = ticket["wait_hours"]
    if not (hours < 0.5):
        if ticket["tier"] == "vip":
            return "priority_queue"
        return "escalation"
    return "normal_queue"
