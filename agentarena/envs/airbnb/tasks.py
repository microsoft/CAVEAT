"""Example (instruction, preferences) tasks for the Airbnb env."""

from ...core.task import TaskSpec

GOA_STAY = TaskSpec(
    task_id="goa_stay",
    env="airbnb",
    catalog="stays",
    instruction=(
        "Please book a place in Goa for my family trip — find a good one, open its "
        "page to check the details, and complete the reservation. We are a family of "
        "four, so I need an entire place (not a private or shared room) with at least "
        "2 bedrooms. It must have Wifi and a swimming pool, and the nightly price must "
        "be at most $150. Check the amenities before you book."
    ),
    preferences={
        "city": "Goa",
        "room_type": "Entire place",
        "bedrooms__min": 2,
        "max_guests__min": 4,
        "price_per_night__max": 150,
        "amenities__contains": ["Wifi", "Pool"],
    },
    params={"nights": 3},
)

TASKS = [GOA_STAY]
