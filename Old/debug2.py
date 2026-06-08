from firecrawl import FirecrawlApp
import re
app = FirecrawlApp(api_key="fc-f5c48a9daab0489cbe4f722d985c7058")
result = app.scrape_url("http://www.cucinaparadiso.com/contacto", formats=["html"])
emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', result.html or "")
wame = re.findall(r'wa\.me/\d+', result.html or "")
print("Emails:", emails[:10])
print("WA:", wame[:5])