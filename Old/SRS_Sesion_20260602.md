# SESIÓN 02/06/2026 — Resumen y pendientes

## Lo que hicimos

### 1. Planilla v8 (leads_restaurantes_CABA_v8.xlsx)
- Agregada columna **Segmento** (Restaurante / Hotel / Evento — futuro)
- **Estado WhatsApp** y **Estado mail** desdoblados (antes era un solo Estado)
- Columna **Origen contacto** (Maps/web / IG-manual / FB-manual / pendiente) — el "candado" del merge
- Columna **Link wa.me** movida junto a Facebook — links IG · FB · wa.me agrupados
- Números WhatsApp normalizados al formato +54 9 11 / +54 11 en toda la planilla
- Links wa.me reconstruidos desde los números y funcionando como hipervínculos

### 2. Google Cloud OAuth2
- Proyecto **Eurocrem n8n** creado en Google Cloud Console
- Google Sheets API y Google Drive API habilitadas
- Credencial OAuth2 creada (n8n sheets) con redirect URL de Railway
- Tela de permissão OAuth configurada (Externo)
- Mail pabloasucari@gmail.com agregado como tester

### 3. n8n — Workflow WhatsApp Camino B
- Workflow importado y conectado a Google Sheets
- Google Sheet creado en Drive: Mi Drive > Eurocrem > Eurocrem_leads
  - Sheet ID: 1MzUe7EtmB9qfntYY29EK6YDijywGEZpffnT1SMSRW8o
  - Hoja: leads_helados_CABA
  - Columnas: place_id | Nombre | Tipo | WhatsApp | Estado_WA | Estado_mail | Fecha_de_alta
- Workflow v3 activo en Railway
- URL del webhook: https://primary-production-5c81.up.railway.app/webhook/eurocrem-whatsapp
- La página HTML con botones funciona — abrís la URL y ves los leads con botón "Abrir WhatsApp"
- El mensaje se personaliza automáticamente con el nombre del local y el tipo (italiano/parrilla/genérico)
- El emoji 👋 fue removido del mensaje (causaba encoding raro en WhatsApp)

### 4. MCP de n8n conectado en Claude Desktop
- Node.js instalado en C:\Program Files\nodejs\
- Config de Claude Desktop actualizado con ruta completa: C:/Program Files/nodejs/npx.cmd
- MCP CLI Proxy autorizado (Allow) — Claude puede ahora leer y modificar workflows de n8n directamente

---

## Pendientes

### Bug activo — nodo Sheets solo lee 1 fila de 3
- El nodo "Leer leads" trae solo Il Matterello aunque hay 3 leads en el Sheet
- Causa probable: formato de fechas distinto entre filas (2/6/2026 vs 2026-05-30) o caracteres invisibles en headers
- **Solución a intentar:** reescribir los headers del Sheet a mano y normalizar fechas
- **Ahora con MCP activo:** puedo revisar y corregir el workflow directamente desde Claude sin tocar n8n

### Números de WhatsApp en el Sheet
- Il Matterello tiene 5511945839898 (número brasileño — es el número de prueba del usuario)
- Tomate tiene 549113628- 3635 (tiene espacio y guión — el código limpia esto automáticamente)
- Cuando se pruebe con restaurantes reales, cargar los números correctos

### Marcar Contactado automáticamente
- El workflow v3 actual NO actualiza el Sheet después de enviar
- Hay que agregar el nodo de update de Sheets de vuelta
- Se puede hacer ahora con el MCP conectado

### Próximos pasos en orden
1. Arreglar el bug de las 3 filas (con MCP desde Claude)
2. Agregar nodo "Marcar Contactado" de vuelta al workflow
3. Probar el flujo completo con números reales de restaurantes
4. Agregar más leads al Sheet (hoy solo hay 3 de prueba)
5. Escalar con el barrido sistemático de barrios (sección 8 del MD de instrucciones)
6. En el futuro: WhatsApp Business Cloud API para envío 100% automático (Camino A)

---

## Archivos del proyecto
| Archivo | Estado |
|---|---|
| leads_restaurantes_CABA_v8.xlsx | ✅ Planilla principal — 21 leads Palermo/Recoleta |
| INSTRUCCIONES_PROYECTO_leads_helados.md | ✅ Contexto completo del proyecto |
| FLUJO_completo_prospeccion.md | ✅ Flujo funcional + WhatsApp Cloud API + Email |
| PLANTILLAS_mensajes_outreach.md | ✅ Plantillas WA (×3) y mail |
| n8n_workflow_whatsapp_v3.json | ✅ Workflow activo en Railway |
| SRS_Sesion_20260602.md | ✅ Este archivo |

## Datos técnicos clave
- n8n Railway URL: https://primary-production-5c81.up.railway.app
- Webhook WhatsApp: https://primary-production-5c81.up.railway.app/webhook/eurocrem-whatsapp
- Google Sheet ID: 1MzUe7EtmB9qfntYY29EK6YDijywGEZpffnT1SMSRW8o
- MCP n8n server: https://primary-production-5c81.up.railway.app/mcp-server/http
- Node.js path: C:/Program Files/nodejs/npx.cmd
