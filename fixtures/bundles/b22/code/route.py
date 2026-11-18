def route(ticket):
    category = ticket["category"]
    if category is None:
        return "manual_review"
    if ticket["urgent"] and category != "spam":
        return "priority_queue"
    if ticket["wait_minutes"] >= 20:
        if ticket["tier"] == "vip":
            return "priority_queue"
        return "escalation"
    return "normal_queue"
