from firecrawl import FirecrawlApp
app = FirecrawlApp(api_key="fc-f5c48a9daab0489cbe4f722d985c7058")
result = app.scrape_url("http://www.cucinaparadiso.com/", formats=["markdown"])
print(result.markdown[-3000:])