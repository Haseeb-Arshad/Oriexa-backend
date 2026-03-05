from pydantic import BaseModel
from typing import Optional

class Task:
    def __init__(self, max_revisions):
        self.max_revisions = max_revisions

class Deliverable:
    def __init__(self, revision_number):
        self.revision_number = revision_number

def check_submit(latest_rev, max_revisions):
    next_revision = (latest_rev + 1) if latest_rev is not None else 1
    if next_revision > max_revisions + 1:
        return f"Error: Max revisions reached ({next_revision - 1} of {max_revisions})"
    return f"Success: Next revision is {next_revision}"

def check_request(del_rev, max_revisions):
    if del_rev >= max_revisions + 1:
        return f"Error: Max revisions reached ({del_rev} of {max_revisions + 1} deliveries)"
    return "Success: Revision requested"

print("--- Submit Deliverable Test ---")
print("Max 1, Latest None:", check_submit(None, 1))
print("Max 1, Latest 1:", check_submit(1, 1))
print("Max 1, Latest 2:", check_submit(2, 1))

print("\n--- Request Revision Test ---")
print("Max 1, Del 1:", check_request(1, 1))
print("Max 1, Del 2:", check_request(2, 1))
