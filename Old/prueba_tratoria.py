import httpx, re

with httpx.Client(verify=False, follow_redirects=True) as c:
    r = c.get('https://www.trattoriaolivetti.com/', timeout=12)
    html = r.text

print('Bytes:', len(html))
matches = re.findall(r'.{80}whatsapp.{80}', html, re.I)
for m in matches[:5]:
    print(repr(m))
    print()