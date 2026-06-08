-- ============================================================
-- EUROCREM — Schema Supabase
-- Versión: 1.0 — 03/06/2026
-- ============================================================

-- ============================================================
-- TABLA: leads
-- 1 fila = 1 restaurante
-- ============================================================

CREATE TABLE leads (
    -- Identidad
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    place_id            TEXT UNIQUE,           -- Clave de dedup (Google Places). Puede ser NULL si no viene de Maps.

    -- Clasificación
    segmento            TEXT DEFAULT 'Restaurante', -- Restaurante / Hotel / Evento
    fit                 TEXT,                  -- Alto / Medio / Bajo / Muy bajo
    canal_preferido     TEXT,                  -- WhatsApp / Mail / Visita

    -- Datos del lugar (Maps — actualizables por batch)
    nombre              TEXT NOT NULL,
    direccion           TEXT,
    barrio              TEXT,
    tipo                TEXT,                  -- Italiano, Parrilla premium, Bodegón, etc.
    google_nota         NUMERIC(2,1),          -- Ej: 4.3
    google_resenas      INTEGER,
    precio              TEXT,                  -- $, $$, $$$
    horarios            TEXT,                  -- JSON string con horarios por día
    abierto             BOOLEAN,               -- Estado actual según Maps

    -- Contacto (mix automático + manual — ver origen_contacto)
    telefono            TEXT,                  -- Teléfono de Maps (actualizable)
    whatsapp            TEXT,                  -- Número WhatsApp normalizado (+54 9 11...)
    email               TEXT,                  -- Email de contacto
    sitio_web           TEXT,
    instagram           TEXT,                  -- Handle @
    link_ig             TEXT,                  -- URL directa al perfil
    facebook            TEXT,                  -- URL página FB
    link_wame           TEXT,                  -- Link wa.me directo (clic = abre chat)
    reservas            TEXT,                  -- Meitre / Woki / propio / etc.

    -- Enriquecimiento manual
    decisor             TEXT,                  -- Nombre dueño / gerente / área eventos
    capacidad_eventos   TEXT,                  -- Nº comensales o "a confirmar"
    helado_propio       TEXT,                  -- No / Sí: descripción
    guias               TEXT,                  -- Michelin, Bib Gourmand, TripAdvisor, etc.
    notas               TEXT,                  -- Notas libres del vendedor

    -- Control de origen (determina qué puede pisar el batch)
    origen_contacto     TEXT DEFAULT 'pendiente',
    -- Valores:
    --   Maps-web   → vino de Places API o scraping automático (batch puede actualizar)
    --   IG-manual  → cargado a mano desde Instagram (NUNCA pisar)
    --   FB-manual  → cargado a mano desde Facebook (NUNCA pisar)
    --   web-auto   → encontrado por batch en Linktree/web/TripAdvisor
    --   pendiente  → no encontrado todavía

    -- Estado de contacto por canal
    estado_wa           TEXT DEFAULT 'No contactado',
    -- Valores: No contactado / Contactado / Respondió / Baja
    estado_mail         TEXT DEFAULT 'No contactado',
    -- Valores: No contactado / Contactado / Respondió / Baja

    -- Flags
    opt_in              BOOLEAN DEFAULT FALSE,
    enriquecido         BOOLEAN DEFAULT FALSE, -- TRUE cuando hermano completó datos manuales
    apto_difusion       BOOLEAN DEFAULT FALSE, -- TRUE cuando el restaurante agendó el número

    -- Auditoría
    fecha_alta          DATE DEFAULT CURRENT_DATE,
    fecha_actualizacion TIMESTAMPTZ DEFAULT NOW()
);

-- Índices útiles
CREATE INDEX idx_leads_place_id    ON leads(place_id);
CREATE INDEX idx_leads_barrio      ON leads(barrio);
CREATE INDEX idx_leads_fit         ON leads(fit);
CREATE INDEX idx_leads_estado_wa   ON leads(estado_wa);
CREATE INDEX idx_leads_estado_mail ON leads(estado_mail);
CREATE INDEX idx_leads_enriquecido ON leads(enriquecido);
CREATE INDEX idx_leads_origen      ON leads(origen_contacto);

-- Trigger: actualiza fecha_actualizacion automáticamente
CREATE OR REPLACE FUNCTION update_fecha_actualizacion()
RETURNS TRIGGER AS $$
BEGIN
    NEW.fecha_actualizacion = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_leads_updated
BEFORE UPDATE ON leads
FOR EACH ROW
EXECUTE FUNCTION update_fecha_actualizacion();


-- ============================================================
-- TABLA: mensajes
-- 1 fila = 1 mensaje enviado o recibido
-- ============================================================

CREATE TABLE mensajes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id     UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

    canal       TEXT NOT NULL,      -- whatsapp / email
    direccion   TEXT NOT NULL,      -- enviado / recibido
    contenido   TEXT,               -- Texto del mensaje
    estado      TEXT,               -- enviado / entregado / leido / error / rebotado

    -- Metadatos WhatsApp
    wamid       TEXT,               -- ID de Meta para tracking de estado
    plantilla   TEXT,               -- Nombre de la plantilla usada (si aplica)

    -- Metadatos email
    message_id  TEXT,               -- Message-ID del email para threading
    asunto      TEXT,

    fecha       TIMESTAMPTZ DEFAULT NOW()
);

-- Índices
CREATE INDEX idx_mensajes_lead_id ON mensajes(lead_id);
CREATE INDEX idx_mensajes_canal   ON mensajes(canal);
CREATE INDEX idx_mensajes_fecha   ON mensajes(fecha);


-- ============================================================
-- VISTA ÚTIL: leads pendientes de enriquecimiento
-- ============================================================

CREATE VIEW v_leads_pendientes AS
SELECT
    id, nombre, barrio, tipo, fit,
    telefono, whatsapp, email, instagram,
    origen_contacto, estado_wa, estado_mail,
    fecha_alta
FROM leads
WHERE enriquecido = FALSE
ORDER BY
    CASE fit
        WHEN 'Alto'     THEN 1
        WHEN 'Medio'    THEN 2
        WHEN 'Bajo'     THEN 3
        WHEN 'Muy bajo' THEN 4
        ELSE 5
    END,
    fecha_alta DESC;


-- ============================================================
-- VISTA ÚTIL: leads listos para envío
-- ============================================================

CREATE VIEW v_leads_para_envio AS
SELECT
    id, nombre, barrio, tipo, fit,
    whatsapp, email, canal_preferido,
    estado_wa, estado_mail,
    decisor, notas
FROM leads
WHERE
    fit IN ('Alto', 'Medio')
    AND (
        (whatsapp IS NOT NULL AND estado_wa   = 'No contactado')
        OR
        (email    IS NOT NULL AND estado_mail = 'No contactado')
    )
ORDER BY
    CASE fit WHEN 'Alto' THEN 1 WHEN 'Medio' THEN 2 ELSE 3 END,
    google_resenas DESC NULLS LAST;
