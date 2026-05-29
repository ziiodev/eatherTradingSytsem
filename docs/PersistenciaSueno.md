> **Nota (sleep-learning-loop, archivado)**: El esquema de persistencia descrito aquí está implementado en migración `0011_sleep_learning_loop.py` con 4 tablas: `q_tables`, `episodic_memory`, `semantic_memory`, `sleep_reports` (más extensión de `config_versions`). Ver spec canónico `specs/sleep-learning` y `sdd/sleep-learning-loop/spec/db-schema-delta` en engram.

 Excelente punto. Este es uno de los aspectos más importantes (y a menudo subestimados) en un sistema de agentes que aprenden.Si el VPS se cae, todo el conocimiento adquirido (Q-Table, memorias episódicas, reglas aprendidas, versiones de configuración, etc.) no puede perderse. Debe sobrevivir y poder restaurarse rápidamente.Estrategia Recomendada de Persistencia (Resistente a caídas)Aquí te propongo la arquitectura de persistencia más robusta y práctica:1. Base de Datos Centralizada (PostgreSQL) - El cerebro persistenteTodo el conocimiento valioso debe guardarse en la DB (que debería estar en un servidor separado o en un servicio gestionado como Neon, Supabase, Railway, etc.).Tablas clave para preservar el conocimiento:projects → Configuración actual + versión actual
q_tables project_id
version (ej: Q-v42)
table_data (JSONB o archivo binario)
learning_rate
created_at
metadata (estadísticas de entrenamiento)

episodic_memory (Memoria de experiencias)project_id
trade_id
state (JSONB: tendencia, volatilidad, hora, etc.)
action
reward (%)
outcome
worker_reasoning (texto)
timestamp

semantic_memory (Reglas y conocimiento)project_id
rule_type (filtro_horario, risk_management, etc.)
content
confidence
source (sueño_profundo, micro_sueño, manual)

project_versions (Versionado completo)Configuración completa + prompts + Q-Table version + estado en un JSONB grande.

sleep_reports (Todos los reportes de sueño)

2. Cómo hacer que sea realmente resistenteGuardar frecuentemente:Después de cada trade cerrado → guardar episodic memory + actualizar Q-Table.
Al final de cada Micro-sueño → guardar cambios.
Al final de cada Sueño Profundo → guardar todo (versión completa).

Checkpointing en LangGraph:Usa checkpointer de LangGraph con PostgreSQL (LangGraph tiene soporte nativo para PostgresCheckpointer). Esto guarda el estado completo del grafo.

Almacenamiento híbrido:Datos estructurados → PostgreSQL
Q-Table grande → Guardar también como archivo .pkl o Parquet en un volumen persistente + backup en S3/Cloud Storage.
Vector DB (si usas embeddings) → PGVector o Chroma persistente.

Recuperación automática:Al iniciar el sistema (o recrear el contenedor), el Orquestador hace:Carga la última versión de Q-Table
Carga la memoria episódica reciente
Carga las reglas semanticas
Carga el último checkpoint de LangGraph

Flujo de Recuperación tras caida del VPSSe levanta de nuevo el contenedor Docker.
El backend detecta que es un reinicio.
Carga automáticamente la última configuración + Q-Table + memorias desde la DB.
El Worker arranca con todo el conocimiento previo (no empieza desde cero).
Se hace un Micro-sueño rápido para sincronizar contexto reciente.

Recomendaciones adicionales fuertesSepara la DB del VPS: La base de datos debería estar fuera del VPS (en la nube o en otro servidor). Así aunque mueran todos los contenedores MT5, el conocimiento sobrevive.
Backups automáticos: Haz backup diario de la DB + snapshots de volúmenes críticos.
Versiónado agresivo: Nunca sobrescribas completamente. Siempre guarda versiones.
Export/Import: Ten un botón en la web para “Exportar conocimiento completo del proyecto” (JSON grande).

¿Quieres que te prepare ahora:El esquema SQL completo de las tablas de persistencia (Q-Table, memorias, versiones)?
Código de ejemplo de cómo cargar/guardar la Q-Table y memorias?
El prompt actualizado del Orquestador para manejar reinicios y recuperación?

Dime qué parte quieres desarrollar primero y lo hacemos. Esta capa de persistencia es lo que va a hacer que tu sistema sea verdaderamente valioso a largo plazo.

### Estrutura de datos
-- =============================================
-- ESQUEMA DE PERSISTENCIA - AETHER TRADING SYSTEM
-- =============================================

-- Tabla principal de proyectos
CREATE TABLE projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL,
    
    name                VARCHAR(120) NOT NULL,
    symbol              VARCHAR(20) NOT NULL,
    timeframe           VARCHAR(10) NOT NULL,
    status              VARCHAR(25) DEFAULT 'inactive',
    
    -- Docker / Infra
    container_id        VARCHAR(100),
    container_name      VARCHAR(100) UNIQUE,
    mcp_url             VARCHAR(255),
    
    -- Cuenta
    account_login       VARCHAR(50),
    account_server      VARCHAR(100),
    
    -- Configuración de riesgo
    capital_asignado    DECIMAL(18,2),
    risk_per_trade      DECIMAL(5,2) DEFAULT 1.00,
    max_daily_dd        DECIMAL(5,2) DEFAULT 3.00,
    max_total_dd        DECIMAL(5,2) DEFAULT 8.00,
    
    strategy_version    INTEGER DEFAULT 1,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    last_sleep_at       TIMESTAMPTZ,
    last_micro_sleep_at TIMESTAMPTZ
);

-- ====================== Q-TABLE PERSISTENCE ======================
CREATE TABLE q_tables (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    
    version             VARCHAR(30) NOT NULL,           -- Ej: Q-v47, Q-20250528-01
    table_data          JSONB NOT NULL,                 -- Aquí se guarda toda la Q-Table (o referencia a archivo)
    learning_rate       DECIMAL(5,4) DEFAULT 0.25,
    discount_factor     DECIMAL(5,4) DEFAULT 0.92,
    
    total_trades        INTEGER DEFAULT 0,
    total_reward        DECIMAL(12,4) DEFAULT 0,
    metadata            JSONB,                          -- estadísticas, estados usados, etc.
    
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    applied_at          TIMESTAMPTZ,
    
    UNIQUE(project_id, version)
);

-- ====================== MEMORIA EPISÓDICA ======================
CREATE TABLE episodic_memory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    
    trade_id            VARCHAR(50),                    -- MagicNumber o ID interno
    timestamp           TIMESTAMPTZ NOT NULL,
    
    state               JSONB NOT NULL,                 -- tendencia, volatilidad, hora, etc.
    action              VARCHAR(50) NOT NULL,           -- open_long, open_short, close, etc.
    reward              DECIMAL(10,4) NOT NULL,         -- % de equity
    result              JSONB,                          -- profit, duration, etc.
    
    worker_reasoning    TEXT,
    q_value_before      DECIMAL(10,4),
    q_value_after       DECIMAL(10,4),
    
    is_special          BOOLEAN DEFAULT FALSE,          -- trade destacado (gran ganancia o pérdida)
    sleep_phase_id      UUID                            -- a qué fase de sueño pertenece
);

-- ====================== MEMORIA SEMÁNTICA (Reglas) ======================
CREATE TABLE semantic_memory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    
    rule_type           VARCHAR(50) NOT NULL,           -- risk_management, timing, filter, etc.
    title               VARCHAR(150) NOT NULL,
    content             TEXT NOT NULL,
    confidence          DECIMAL(4,2) DEFAULT 0.80,      -- 0.0 a 1.0
    source              VARCHAR(30),                    -- deep_sleep, micro_sleep, manual
    version             INTEGER DEFAULT 1,
    
    active              BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ====================== VERSIONADO COMPLETO ======================
CREATE TABLE project_versions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    
    version_number      INTEGER NOT NULL,
    version_name        VARCHAR(80),
    
    full_config         JSONB NOT NULL,                 -- Toda la config + parámetros
    q_table_version     VARCHAR(30),
    prompt_snapshot     TEXT,                           -- snapshot de prompts importantes
    
    reason              TEXT,                           -- "Mejora tras Sueño Profundo", "Ajuste manual", etc.
    created_by          VARCHAR(30),                    -- "system", "user", "auditor"
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ====================== REPORTES DE SUEÑO ======================
CREATE TABLE sleep_reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    
    sleep_type          VARCHAR(20) NOT NULL,           -- micro_sleep, deep_sleep, critical
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ NOT NULL,
    
    summary             TEXT,
    auditor_metrics     JSONB,
    worker_insights     JSONB,
    improvements_applied JSONB,
    q_table_before      VARCHAR(30),
    q_table_after       VARCHAR(30),
    
    overall_score       DECIMAL(5,2)
);

-- Índices recomendados
CREATE INDEX idx_episodic_project_time ON episodic_memory(project_id, timestamp);
CREATE INDEX idx_qtables_project ON q_tables(project_id);
CREATE INDEX idx_semantic_project ON semantic_memory(project_id, active);
CREATE INDEX idx_versions_project ON project_versions(project_id, version_number);

-- Comentarios
COMMENT ON TABLE episodic_memory IS 'Memoria de experiencias del Worker para aprendizaje';
COMMENT ON TABLE q_tables IS 'Histórico versionado de Q-Tables';
COMMENT ON TABLE semantic_memory IS 'Reglas y conocimiento semántico aprendido';

