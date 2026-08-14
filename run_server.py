"""Launcher — changes to the correct directory before starting uvicorn."""
import os
import sys

# Change working directory so pydantic-settings finds .env
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


They mean this:

The first point says the product page should not stop at basic part info like part number, stock, and price. It should also show higher-value decision signals that help a buyer judge whether the part is safe to use and worth buying. Things like lifecycle status, end-of-life risk, last-time-buy dates, sourcing risk, country of origin, manufacturing footprint, and compliance status should be turned into simple, readable insights. The page should help the buyer answer questions like: Is this part stable? Is it risky? Is supply concentrated? Should I consider a backup?

The second point says the page should also surface important change signals over time. If something about the part has changed or may change soon, like a product change notice, discontinuation notice, lead-time shift, stock drop, manufacturing-site change, or compliance change, the buyer should know. But it should be shown in a controlled way, not as scary warnings everywhere. The idea is to communicate: this part is actively being monitored, and the platform is helping the buyer stay ahead of changes that could affect purchasing or long-term supply.

In short:
- The first point is about showing strategic part intelligence.
- The second point is about showing ongoing risk and change monitoring.

If you want, I can rewrite those two sections into much clearer plain English for the PDF.