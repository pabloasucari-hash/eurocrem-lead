from firecrawl import FirecrawlApp
import re

app = FirecrawlApp(api_key="fc-f5c48a9daab0489cbe4f722d985c7058")

result = app.scrape_url(
    "http://www.cucinaparadiso.com/contacto",
    formats=["markdown"],
    only_main_content=False
)

print(result.markdown[:5000])