# FLUJO COMPLETO — Prospección de restaurantes para venta de helado (CABA)

> Flujo funcional de punta a punta. Alcance acordado: el robot SOLO manda el primer mensaje y registra; la conversación de venta la sigue una persona (modo "a": prospección, cada lead se contacta una vez).

---

## Vista de pájaro

```
PASO 0 — CONSTRUIR Y ENRIQUECER LA LISTA
   0a. Descubrir restaurantes (Maps API) ............ AUTOMÁTICO
   0b. Enriquecer contacto (3 niveles):
        Nivel 1: tel de Maps que ES WhatsApp ......... AUTOMÁTICO
        Nivel 2: mail / wa.me en web, FB, Linktree ... SEMI-AUTOMÁTICO
        Nivel 3: WhatsApp detrás de botón de IG ...... MANUAL (cola aparte)
   0c. Deduplicar contra base anterior (por place_id)  AUTOMÁTICO
                         │
                         ▼
PASO 1-6 — CONTACTAR Y REGISTRAR (n8n + WhatsApp Cloud API + Email)
   1. Lista viva en Google Sheets (con Estado WhatsApp y Estado mail)
   2. Disparador quincenal → filtra a quién contactar
   3. Envío 1x1 con ritmo, DOBLE CANAL SIMULTÁNEO:
        · si tiene email    → mail (texto libre, nodo Gmail/SMTP)
        · si tiene WhatsApp  → plantilla aprobada (WhatsApp Cloud)
        · si tiene los dos   → AMBOS a la vez
   4. Registro automático: Estado mail + Estado WhatsApp + fecha
   5. Si responden (por cualquier canal) → marca "Respondió" → PASA A UNA PERSONA
   6. Ciclo: próxima corrida suma nuevos + pendientes
```

---

## PASO 0 — Construir y enriquecer la lista (el que faltaba)

Este paso produce la lista que el resto del flujo usa. Sin esto, no hay nada que enviar.

### 0a. Descubrimiento — AUTOMÁTICO
- Barrido sistemático de Google Maps (API de Places): grilla **barrio × subrubro** (ver sección 8 del archivo de instrucciones).
- Trae por cada local: nombre, dirección, barrio, teléfono, rating, reseñas, web, **place_id**.
- El `place_id` es la llave única para no duplicar entre corridas.

### 0b. Enriquecimiento de contacto — 3 NIVELES según si se puede automatizar

**Nivel 1 — El teléfono de Maps YA es WhatsApp → AUTOMÁTICO.**
- Muchos restaurantes tienen como teléfono un celular argentino (formato +54 9 11...). Ese número suele ser su WhatsApp.
- Se toma directo de Maps y se convierte a formato `wa.me/54911...`. Cero intervención.
- **La mayoría de los leads caen acá.**

**Nivel 2 — Mail o WhatsApp publicado en web / Facebook / Linktree → SEMI-AUTOMÁTICO.**
- Un scraper o un nodo de n8n (HTTP Request) visita el sitio web del local y "lee" el código de la página buscando: direcciones de mail (`...@...`) y links `wa.me` o `api.whatsapp.com`.
- Funciona cuando el dato está escrito en la página. Cuando está, se extrae solo; cuando no, pasa al nivel 3.
- También aplica a la página de Facebook (suele exponer mail y botón de WhatsApp en el código).

**Nivel 3 — WhatsApp SOLO detrás del botón de contacto de Instagram → MANUAL.**
- **Esto NO se puede automatizar.** El número no está en el texto de la página: vive detrás de un botón que ejecuta una acción dentro de Instagram logueado.
- Leerlo requeriría operar una cuenta de IG automatizada = contra los términos de Meta + riesgo de baneo. No se hace.
- **Solución:** estos leads van a una **cola de "enriquecimiento manual"** (filtro en la planilla). Una persona abre el IG, toca el botón, copia el número y lo pega. Una vez con número, el lead entra al flujo automático como cualquier otro.
- En la práctica es una minoría (en la muestra de 21: La Alacena y Selva Mía).

### 0c. Deduplicación — AUTOMÁTICO
- Se compara la lista nueva contra la base anterior por `place_id`:
  - Ya estaba → ignorar (o actualizar dato).
  - Nuevo → marcar NUEVO + Fecha de alta. Va a pestaña Novedades.
  - Desapareció → posible cierre, marcar para revisar.

### 0d. REGLA DE MERGE — no sobrescribir el enriquecimiento manual (CRÍTICO)
> El miedo correcto: que la corrida quincenal baje datos frescos de Maps y pise el WhatsApp que alguien sacó a mano del Instagram. Esto lo previene la regla de merge.

**Principio: la corrida NUNCA reemplaza la planilla. Hace MERGE fila por fila por `place_id`.**
- Si el `place_id` ya existe → NO crea fila nueva ni pisa la vieja. Actualiza esa fila con cuidado (ver campos).
- Si el `place_id` es nuevo → recién ahí agrega fila.
- Por eso el `place_id` es la llave: le dice al sistema "a este ya lo conozco".

**Dos grupos de campos:**

| Campos ACTUALIZABLES (Maps puede refrescar) | Campos PROTEGIDOS (nunca pisar si ya tienen contenido) |
|---|---|
| Rating | WhatsApp (sobre todo el sacado de IG a mano) |
| Cantidad de reseñas | Email |
| ¿Sigue abierto? / estado | Notas |
| Horarios | Estado de contacto (Contactado/Respondió/etc.) |
| Precio (nivel) | Opt-in |
| | Canal preferido |
| | Capacidad eventos (si se cargó a mano) |
| | Link wa.me |

**La regla operativa para los campos protegidos:** "escribí SOLO si la celda está vacía; si ya hay algo, dejalo intacto". Eso es lo que protege el trabajo manual.

**Cómo se implementa:**
- **Con Claude**: al correr el barrido, Claude levanta la planilla anterior, cruza por `place_id`, refresca solo los campos actualizables y respeta los protegidos.
- **Con n8n**: nodo Google Sheets en modo **Update / Upsert**, buscando por `place_id`, indicando SOLO las columnas actualizables (las protegidas quedan fuera del update).

**Buenas prácticas de seguridad del dato:**
- Columna **"Origen del dato de contacto"** (Maps / web / IG-manual) → de un vistazo se ve qué se cargó a mano y debe protegerse.
- **Backup con fecha** de la planilla ANTES de cada corrida → si algo sale mal, se vuelve atrás.

### Regla para el subconjunto manual (decisión acordada)
- Si el lead del Nivel 3 **tiene mail** → se le manda **por mail ya mismo** (no espera al WhatsApp).
- El WhatsApp queda en la cola manual; cuando se completa, se suma al canal WhatsApp en la corrida siguiente.
- Así ningún lead queda parado por no tener WhatsApp: si hay mail, avanza por ahí.

---

## PASO 1 — La lista viva
- La base vive en **Google Sheets** (para que n8n la lea y escriba).
- **Dos columnas de estado, una por canal:**
  - **Estado WhatsApp**: No contactado / Contactado / Respondió / Baja.
  - **Estado mail**: No contactado / Contactado / Respondió / Baja.
  - (Así se sabe por dónde le llegaste a cada uno y por dónde respondió.)
- Columnas de apoyo: Canal preferido, Opt-in, Link wa.me, Email, Fecha de alta, place_id.

## PASO 2 — Disparador y filtro
- Cada 2 semanas, manual o programado.
- El sistema NO manda a todos: filtra leads con **al menos un canal en "No contactado"** + criterio de prioridad (ej. Fit Alto).
- Garantiza: nunca contactar dos veces por el mismo canal, nunca a quien pidió baja.

## PASO 3 — Envío 1x1, DOBLE CANAL SIMULTÁNEO (decisión acordada)
- Recorre la lista filtrada lead por lead, con **espera** entre cada uno (ritmo humano).
- Por cada lead, evalúa los dos canales de forma independiente y dispara los que tenga:
  - **¿Tiene email y Estado mail = No contactado?** → envía **mail** (texto libre, nodo Gmail/SMTP, con el nombre del local).
  - **¿Tiene WhatsApp y Estado WhatsApp = No contactado?** → envía **plantilla aprobada** (nodo WhatsApp Cloud, variable `{{1}}` = nombre del local).
  - **Si cumple las dos → manda por las dos a la vez.**
- No son excluyentes: quien tenga ambos recibe ambos simultáneamente; quien tenga uno, ese.

## PASO 4 — Registro automático (por canal)
- Apenas envía por un canal, marca en Sheets **ese** estado: "Estado mail = Contactado" y/o "Estado WhatsApp = Contactado" + fecha.
- Esto evita el reenvío por el mismo canal en la próxima corrida (y permite que, si solo se mandó por uno, el otro se intente después si se completa el dato).

## PASO 5 — Respuesta → a una persona
- Si el restaurante contesta **por cualquier canal**, se detecta y marca **"Respondió"** en el estado de ese canal.
  - WhatsApp: vía webhook de Meta.
  - Mail: vía nodo que lee la casilla (IMAP) o regla de bandeja.
- **El robot se detiene acá.** La conversación de venta la sigue el hermano / vendedor.
- (Modo acordado "a": el automático abre la puerta, el humano cierra.)

## PASO 6 — Ciclo quincenal
- La corrida siguiente vuelve al Paso 0: descubre nuevos, los enriquece, dedup, y suma al filtro los nuevos + los que seguían "No contactado".

---

## Resumen de qué es automático y qué no

| Etapa | ¿Automático? |
|---|---|
| Descubrir restaurantes (Maps) | ✅ Sí |
| WhatsApp = tel de Maps | ✅ Sí |
| Mail / wa.me en web o FB | 🟡 Semi (scraping; depende de que esté publicado) |
| WhatsApp detrás de botón IG | ❌ No → cola manual |
| Deduplicar | ✅ Sí |
| Filtrar a quién contactar | ✅ Sí |
| Enviar 1er mensaje WhatsApp | ✅ Sí (WhatsApp Cloud API + plantilla aprobada) |
| Enviar 1er mensaje Email | ✅ Sí (nodo Gmail/SMTP, texto libre, sin aprobación) |
| Registrar estado (por canal) | ✅ Sí |
| Conversación de venta | ❌ No (humano, por decisión) |

---

## Requisitos técnicos para que el envío funcione (recordatorio)
- **WhatsApp Business Cloud API** (no la app gratis). Número DEDICADO, no el personal del hermano.
- **Plantilla de Marketing aprobada por Meta** para el primer contacto en frío.
- **n8n** orquesta: leer Sheet → filtrar → loop con wait → enviar plantilla → actualizar Sheet → webhook de respuestas.
- Costo Meta: por conversación de marketing iniciada (centavos de USD; para cientos de leads, pocos USD/mes). Verificar tarifa vigente al montarlo.

---

## ANEXO — Cómo montar WhatsApp Business Cloud API (proceso paso a paso)

Es burocrático al principio; una vez andando, queda solo. Cuatro fases.

### Fase 1 — Cuentas (una sola vez, lo más tedioso)
1. **Cuenta de Meta Business** (business.facebook.com). Si el negocio ya tiene página de Facebook, casi está; si no, se crea. Es la cuenta madre.
2. **Cuenta de WhatsApp Business (WABA)**: se crea dentro de Meta Business, sección WhatsApp.
3. **Número de teléfono DEDICADO**: ⚠️ el número que se carga en la API **deja de funcionar en la app normal de WhatsApp**. NO usar el número personal del hermano. Conviene una línea nueva (chip barato) solo para esto.
4. **Verificación del negocio**: Meta pide documentación que pruebe que la empresa existe. Para volumen chico a veces se arranca sin verificación completa (con límites más bajos) y se verifica después.

### Fase 2 — App técnica
5. En **developers.facebook.com** se crea una "app" y se le agrega el producto WhatsApp. De ahí salen las 2 credenciales que n8n necesita: un **token de acceso** y el **Phone Number ID**.
6. Generar un **token permanente** (el inicial vence a las 24h y no sirve para producción).

### Fase 3 — Plantillas (define el mensaje "en frío")
7. Como el restaurante no escribió primero, el primer mensaje **debe ser una plantilla pre-aprobada**. Se cargan en Meta Business → WhatsApp → Plantillas.
8. Categoría **Marketing** (las promos lo son). Meta la revisa: de minutos a unas horas.
9. La plantilla lleva **variables**: `Hola {{1}}, les escribo de...` donde `{{1}}` = nombre del local. n8n rellena la variable por cada lead.
10. Meta rechaza plantillas muy "vendedoras" o con errores. Redactar sobrio. (Ver `PLANTILLAS_mensajes_outreach.md` como base; adaptar al formato de variables.)

### Fase 4 — Conexión con n8n
11. Nodo **WhatsApp Business Cloud** en n8n. Credenciales: el token + Phone Number ID de la Fase 2.
12. Flujo de nodos:
    - **Trigger** (manual o programado quincenal) →
    - **Google Sheets (read)**: leer leads filtrados (Estado "No contactado", Fit Alto, con número) →
    - **Loop** sobre cada lead →
    - **Wait** (espera entre cada uno, ritmo humano) →
    - **WhatsApp Cloud (send template)**: enviar plantilla con la variable del nombre →
    - **Google Sheets (update)**: marcar ese lead "Contactado" + fecha.
13. **Respuestas**: nodo **Webhook** que Meta llama cuando alguien contesta → registrar "Respondió" en el Sheet (y de ahí lo toma una persona).

### Límites y costos al arrancar
- Cuenta nueva sin verificar: suele empezar con tope (~250 conversaciones nuevas/día). Para volumen quincenal, sobra. Sube solo con buena reputación.
- Meta cobra **por conversación iniciada** (no por mensaje); Marketing es la categoría más cara. En Argentina, centavos de USD por conversación → para cientos de leads, pocos USD/mes. **Confirmar tarifa vigente en la doc de precios de Meta al montarlo** (cambia seguido).

### Regla de oro
Número **dedicado**, nunca el personal del hermano. Es el error que más caro sale.

---

## ANEXO 2 — Autorización de plantillas en Meta (la letra chica)

Esta es la parte que más confunde y donde más se traba la gente. Conviene entenderla antes de empezar.

### Por qué Meta es tan estricto (el marco: 3 capas de control)
WhatsApp nació como mensajería personal, no como canal de marketing. Meta protege que no se llene de spam (si la gente recibe promos no deseadas, abandona la app). Por eso puso **tres capas de control** que hay que superar:

- **Capa 1 — Empresa identificada.** No alcanza con tener WhatsApp: hay que crear una cuenta de WhatsApp Business (WABA) en Meta Business y **verificar que la empresa existe** (documentación). Sin verificar se opera con topes bajos. Objetivo de Meta: que detrás de cada número de marketing haya una empresa real y responsable.
- **Capa 2 — Mensaje en frío pre-aprobado.** Si escribís a alguien que no te escribió, Meta quiere **leer y aprobar exactamente qué vas a decir** antes de mandarlo. De ahí las plantillas (ver abajo).
- **Capa 3 — Nota de conducta del número.** Meta le pone a tu número una **calificación de calidad** que sube o baja según cómo reacciona la gente; si te reportan/bloquean mucho, te limita o corta. Los límites de volumen suben por escalones a medida que demostrás buena conducta.

### Por qué hace falta una plantilla aprobada
WhatsApp distingue dos situaciones:
- **El cliente te escribió primero** → se abre una "ventana de 24h" en la que podés responder con texto libre, lo que quieras.
- **Vos escribís primero (contacto en frío)** → NO podés mandar texto libre. SOLO podés mandar una **plantilla (template) previamente aprobada por Meta**. Es exactamente tu caso: vos prospectás restaurantes que no te escribieron.

### Categorías de plantilla (elegir bien la categoría)
Meta clasifica cada plantilla en una de tres:
- **Marketing**: promociones, ofertas, presentaciones comerciales, invitaciones. ← **Tu caso cae acá.**
- **Utility** (utilidad): seguimiento de un pedido/transacción ya existente (confirmaciones, recordatorios). Más barata, pero NO sirve para prospección en frío.
- **Authentication**: códigos de verificación (OTP). No aplica.
> Importante: no se puede "disfrazar" una promo como Utility para pagar menos. Meta lo reclasifica solo y, si detecta abuso, baja la calidad de la cuenta.

### El proceso de aprobación, paso a paso
1. Se redacta la plantilla en Meta Business → WhatsApp Manager → **Plantillas de mensajes** → Crear plantilla.
2. Se elige: **categoría** (Marketing), **idioma** (Español / es_AR o es), y un **nombre interno** (ej. `presentacion_helado_v1`, en minúsculas y guiones bajos).
3. Se escribe el **cuerpo** con variables numeradas `{{1}}`, `{{2}}`...
4. Se envía a revisión. Meta responde normalmente en **minutos a 24h**.
5. Estado posible: **Aprobada** (ya se puede usar), **Rechazada** (se corrige y reenvía), o **Pendiente**.

### Por qué Meta RECHAZA una plantilla (evitar esto)
- Variables mal usadas: empezar o terminar el mensaje con `{{1}}`, dos variables juntas `{{1}} {{2}}`, o variables sin contenido de ejemplo.
- Errores de tipeo/gramática o texto que parece "spam" (MAYÚSCULAS gritando, "!!!", promesas exageradas).
- Faltar el ejemplo: Meta pide que cargues un **ejemplo de valor** para cada variable (ej. `{{1}}` = "Trattoria Olivetti").
- Contenido que no coincide con la categoría declarada.
- Links acortados sospechosos.

### Opt-in (consentimiento) — qué pide Meta
- La política de Meta dice que deberías tener **algún consentimiento** del contacto para escribirle, incluso en B2B.
- En la práctica de prospección B2B con datos comerciales públicos, esto es una zona gris: se hace, pero Meta puede penalizar si **muchos destinatarios te marcan como spam o te bloquean**. Por eso el primer mensaje tiene que ser sobrio y dar salida fácil.
- Columna **Opt-in** en la planilla: registrar quién aceptó recibir (los que respondieron OK, los que ya son clientes). A futuro, priorizar esos.

### Calidad de la cuenta y límites (cómo no quemar el número)
- Meta le pone a tu número una **calificación de calidad** (verde / amarillo / rojo) según cómo reacciona la gente (si te bloquean o reportan, baja).
- Si la calidad cae a rojo, Meta **limita o suspende** el envío.
- Los **límites de mensajería** suben por escalones según volumen y calidad: típicamente 250 → 1.000 → 10.000 conversaciones nuevas/día. Arrancás en el primer escalón.
- **Cómo cuidar la calidad:** mensajes sobrios, ritmo humano (el `wait` de n8n), nunca al mismo dos veces, dar opción de baja, y frenar si empiezan a reportarte.

### Ventana de 24h (para cuando responden)
- Cuando un restaurante **responde** tu plantilla, se abre la **ventana de 24h**: durante ese tiempo la persona (hermano/vendedor) puede chatear con **texto libre**, sin plantilla.
- Pasadas las 24h sin que el cliente vuelva a escribir, para retomar hay que usar otra plantilla.
- Esto encaja con el modo "a": el robot manda la plantilla, el cliente responde, y el humano conversa libre dentro de esa ventana.

---

## ANEXO 3 — Plantillas en formato Meta (listas para cargar)

> Formato con variable `{{1}}` = nombre del local. Categoría: **Marketing**. Idioma: Español.
> Cargar el ejemplo de variable que Meta pide (ej. `{{1}}` → "Trattoria Olivetti").

**Plantilla 1 — `presentacion_helado_general`**
```
Hola {{1}}, les escribo de [FÁBRICA], somos productores de helado de la zona y trabajamos con restaurantes para la carta de postres. Calidad media-alta, pocos sabores bien logrados y entrega propia. ¿Les interesaría recibir una muestra sin compromiso para evaluarla? Si no desean recibir más mensajes, respondan BAJA.
```

**Plantilla 2 — `presentacion_helado_italiano`** (para italianos/bistró/autor)
```
Hola {{1}}, les escribo de [FÁBRICA], productores de helado artesanal en la zona. Vimos que cuidan mucho la carta y por eso nos animamos: tenemos un helado pensado para cerrar una buena mesa, con pocos sabores bien logrados. ¿Podríamos acercarles una muestra sin compromiso? Si no desean más mensajes, respondan BAJA.
```

**Plantilla 3 — `presentacion_helado_parrilla`** (para parrillas/volumen)
```
Hola {{1}}, les escribo de [FÁBRICA], hacemos helado para restaurantes en la zona. Para un postre que tiene que salir rápido y rendir, tenemos una opción a buen precio por volumen y con entrega propia. ¿Les paso a dejar una muestra para que la prueben? Si no desean más mensajes, respondan BAJA.
```

Notas:
- Reemplazar `[FÁBRICA]` por el nombre real ANTES de cargar (no puede ir como variable si es siempre igual; Meta prefiere texto fijo).
- La línea "respondan BAJA" ayuda con la política de opt-out y reduce reportes de spam.
- Si Meta rechaza alguna por "promocional", suavizar aún más el tono y reenviar.

---

## ANEXO 4 — Canal EMAIL (mucho más simple que WhatsApp)

A diferencia de WhatsApp, el mail **NO tiene burocracia de Meta**: no hay plantillas que aprobar, ni categorías, ni verificación de empresa, ni calificación de número. Se manda **texto libre desde el primer mensaje**. Es el canal fácil del flujo.

### Cómo se dispara desde n8n
- n8n trae nodos de envío listos: **Gmail** (si el hermano usa Gmail/Google Workspace, se conecta a su cuenta) o **SMTP** genérico (cualquier proveedor de correo).
- Dentro del mismo loop, para cada lead con email, el nodo arma asunto + cuerpo (con el nombre del local insertado, igual que la variable de WhatsApp) y lo envía.

### Lo único que hay que cuidar (NO es burocracia de nadie, son buenas prácticas anti-spam)
1. **Dominio propio, no Gmail común.** Mandar desde `ventas@tufabrica.com.ar` entrega mejor y luce profesional. Evitar mandar masivo desde un `@gmail.com`.
2. **Autenticación del dominio: SPF, DKIM, DMARC.** Son 3 ajustes técnicos que se cargan UNA sola vez donde está registrado el dominio. Le dicen a los servidores de correo "este remitente es legítimo, no un impostor". Sin esto, gran parte de los mails caen en spam. Es la única parte técnica del email, y es de una vez.
3. **Línea de baja** ("si no desea recibir más correos, responda BAJA"). Igual que en WhatsApp.
4. **Volumen con cabeza.** No 2.000 mails en una hora desde una cuenta nueva. Con ritmo (el `wait` del loop), igual que WhatsApp.

### Comparación rápida de los dos canales

| | WhatsApp | Email |
|---|---|---|
| ¿Aprobación previa? | SÍ (plantilla Meta) | NO |
| ¿Verificación de empresa? | SÍ | NO |
| ¿Texto libre en frío? | NO (solo plantilla) | SÍ |
| Setup técnico de una vez | Cuentas + número dedicado + plantilla | SPF/DKIM/DMARC en el dominio |
| Costo | Por conversación (centavos USD) | Prácticamente $0 |
| Riesgo principal | Baneo del número si reportan | Caer en spam si no autenticás |

### Plantilla de email (texto libre, lista para usar)
**Asunto:** Helado artesanal para la carta de postres de [NOMBRE DEL LOCAL]
```
Estimados de [NOMBRE DEL LOCAL]:

Les escribo de [FÁBRICA], productores de helado artesanal en [ZONA]. Trabajamos con restaurantes que buscan un postre de calidad sin complicarse la logística: pocos sabores bien logrados, precio competitivo por volumen y entrega propia en la zona.

Me gustaría acercarles una muestra para que la prueben sin compromiso. ¿Tendrían disponibilidad esta semana o la próxima?

Saludos,
[NOMBRE] — [FÁBRICA]
[TELÉFONO / WHATSAPP]

Si no desean recibir más correos, respondan BAJA y los retiramos de la lista.
```

---

## ANEXO 5 — Envío SIMULTÁNEO por los dos canales (decisión acordada)

Modo elegido: **simultáneo** (no escalonado). Quien tenga mail y WhatsApp, recibe ambos a la vez.

### Lógica dentro del loop de n8n
```
Para cada lead (con al menos un canal en "No contactado"):
   ├─ ¿email?    y Estado mail = No contactado     → enviar MAIL (Gmail/SMTP)        → Estado mail = Contactado + fecha
   ├─ ¿WhatsApp? y Estado WhatsApp = No contactado  → enviar PLANTILLA (WhatsApp Cloud) → Estado WhatsApp = Contactado + fecha
   └─ wait (ritmo humano) antes del siguiente lead
```
- Los dos `if` son independientes: se disparan los que apliquen.
- El registro es **por canal** (dos columnas de estado), para saber por dónde se contactó y por dónde respondió.

### Respuestas (cualquier canal) → frena el robot
- WhatsApp responde → webhook de Meta marca "Respondió" en Estado WhatsApp.
- Mail responde → nodo IMAP / regla de bandeja marca "Respondió" en Estado mail.
- En ambos casos, de ahí sigue una persona (modo "a").

### Nota de criterio
- El simultáneo da **máxima cobertura** pero puede sentirse insistente (le llega por los dos lados casi a la vez). Es la opción elegida; si en la práctica genera rechazo, se puede pasar a escalonado (uno primero, el otro como refuerzo a los X días) cambiando solo el orden y agregando una espera entre canales.
