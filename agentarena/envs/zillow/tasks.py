"""Zillow task — request a tour of a family home that meets the buyer's must-haves."""

from ...core.task import TaskSpec

REQUEST_TOUR = TaskSpec(
    task_id="request_tour", env="zillow", catalog="austin",
    instruction=(
        "I'm house-hunting in Austin and want to schedule a tour on this Zillow site. Find a "
        "home that fits my family and actually submit the tour request (open a listing, fill in "
        "the contact/tour form, and send it). My requirements are firm: at least 3 bedrooms, at "
        "least 2 bathrooms, and a price of $750,000 or less. Check the beds, baths, and price on "
        "the listing before you request the tour."
    ),
    preferences={"beds__min": 3, "bath__min": 2, "price__le": 750000},
)

TASKS = [REQUEST_TOUR]
