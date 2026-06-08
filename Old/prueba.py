import httpx, re

with httpx.Client(verify=False, follow_redirects=True) as c:
    r = c.get('https://hierrocasadefuegos.com/qr/', timeout=12)
    html = r.text

# Buscar whatsapp
matches = re.findall(r'.{60}whatsapp.{60}', html, re.I)
for m in matches[:5]:
    print(repr(m))

# Ver todos los links
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'lxml')
for a in soup.find_all('a', href=True):
    print(a['href'], '|', a.get_text().strip())