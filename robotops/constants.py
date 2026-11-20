"""Shared vocabulary for the RobotOps domain."""

ROBOT_STATUSES = ["Operational", "Charging", "Maintenance", "Down"]
AVAILABLE_STATUSES = {"Operational", "Charging"}

SITES = ["San Francisco Lab"]

# model -> (class, default inspection interval in days)
MODELS = {
    "Kestrel": ("Mobile base", 30),
    "Heron": ("Manipulator", 21),
}

INSPECTION_KINDS = ["Routine", "Pre-deployment", "Post-incident", "Safety audit"]
INSPECTION_RESULTS = ["Pass", "Needs attention", "Fail"]

TICKET_PRIORITIES = ["Low", "Medium", "High", "Critical"]
TICKET_STATUSES = ["New", "In progress", "Waiting on parts", "Closed"]
OPEN_TICKET_STATUSES = ("New", "In progress", "Waiting on parts")

TELEOP_TASKS = ["Pick and place", "Cloth folding", "Shelf restocking", "Navigation mapping", "Docking practice"]
TELEOP_STATUSES = ["Scheduled", "In progress", "Completed", "Cancelled"]
OPERATORS = ["Jordan Lee", "Sam Whitaker", "Ravi Menon", "Elena Petrova", "Grace Kim"]

TECHNICIANS = ["Priya Natarajan", "Marcus Bell", "Ana Okafor", "Diego Ramos", "Hana Sato"]
