from firecrawl import FirecrawlApp
import re

app = FirecrawlApp(api_key="fc-f5c48a9daab0489cbe4f722d985c7058")

result = app.scrape_url(
    "http://www.cucinaparadiso.com/",
    formats=["html"],
    actions=[
        {"type": "scroll", "direction": "down", "amount": 3000},
        {"type": "wait", "milliseconds": 2000},
        {"type": "scroll", "direction": "down", "amount": 3000},
        {"type": "wait", "milliseconds": 2000},
    ]
)

html = result.html or ""
emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
wame   = re.findall(r'wa\.me/\d+', html)
print("Emails:", emails[:10])
print("WA:", wame[:5])