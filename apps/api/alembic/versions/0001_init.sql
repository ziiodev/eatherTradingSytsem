-- ----------------------------------------------------------------------------
-- 0001_init.sql — snapshot of the expected schema after `alembic upgrade head`
-- from a clean database (SQUASH migration, accounts-pairs hierarchy).
--
-- Hierarchy: Exchange -> Account (Cuenta) -> Pair (Par) -> Agents.
-- `projects` is renamed `pairs` and reparented onto `accounts`; the broker
-- credential block lives on `accounts`; `project_id` is `pair_id` on every
-- dependent table.
--
-- Generated from a faithful `pg_dump --schema-only` of the live schema
-- produced by 0001_init.py, with the alembic_version bookkeeping table and
-- pg_dump session boilerplate stripped. NOT executed by Alembic; exists for
-- human review.
--
-- If you change 0001_init.py, regenerate this file too.
-- ----------------------------------------------------------------------------

--
--

\restrict Fu43JgXFw1fpUFFBZf8BQ3kGCNKbx6SXqf1taOUjks8uomJIFkgghUEgNJKg5Gb


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    exchange_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    account_login character varying(50),
    account_server character varying(100),
    broker_name character varying(80),
    account_credential_ref character varying(255),
    account_currency character varying(10),
    account_leverage integer,
    account_type character varying(20),
    meta_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    agent_id uuid NOT NULL,
    pair_id uuid NOT NULL,
    started_at timestamp without time zone DEFAULT now() NOT NULL,
    ended_at timestamp without time zone,
    status character varying(20) NOT NULL,
    exit_code integer,
    stdout text,
    stderr text,
    denial_reason text,
    resource_usage jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT agent_runs_running_no_ended CHECK (((((status)::text = 'running'::text) AND (ended_at IS NULL)) OR (((status)::text <> 'running'::text) AND (ended_at IS NOT NULL)))),
    CONSTRAINT agent_runs_status_valid CHECK (((status)::text = ANY ((ARRAY['running'::character varying, 'success'::character varying, 'denied_import'::character varying, 'denied_network'::character varying, 'denied_file'::character varying, 'timeout'::character varying, 'oom'::character varying, 'error'::character varying])::text[])))
);


--
-- Name: agent_skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_skills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id uuid NOT NULL,
    skill_id uuid NOT NULL,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(20) NOT NULL,
    description text,
    logica text NOT NULL,
    runtime character varying(20) DEFAULT 'python'::character varying NOT NULL,
    entrypoint character varying(120),
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT agents_runtime_only_python CHECK (((runtime)::text = 'python'::text)),
    CONSTRAINT agents_type_valid CHECK (((type)::text = ANY ((ARRAY['orchestrator'::character varying, 'investigator'::character varying, 'marker'::character varying, 'worker'::character varying, 'tutor'::character varying, 'auditor'::character varying])::text[])))
);


--
--


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    action character varying(100) NOT NULL,
    target_type character varying(50) NOT NULL,
    target_id uuid,
    before_state jsonb,
    after_state jsonb,
    ip_address inet,
    user_agent text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: chat_action_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_action_proposals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    message_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    pair_id uuid NOT NULL,
    tool_name character varying(80) NOT NULL,
    payload jsonb NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    decided_at timestamp with time zone,
    decided_by uuid,
    decision_note text,
    executed_at timestamp with time zone,
    execution_result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chat_action_proposals_status_valid CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'expired'::character varying, 'executed'::character varying])::text[]))),
    CONSTRAINT chat_action_proposals_tool_name_nonempty CHECK ((length((tool_name)::text) >= 1))
);


--
-- Name: chat_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    title character varying(200) DEFAULT '(sin título)'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    meta_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    tokens_in_total integer DEFAULT 0 NOT NULL,
    usd_estimated_total numeric(12,6) DEFAULT 0 NOT NULL,
    CONSTRAINT chat_conversations_title_nonempty CHECK ((length((title)::text) >= 1)),
    CONSTRAINT chat_conversations_tokens_nonneg CHECK ((tokens_in_total >= 0)),
    CONSTRAINT chat_conversations_usd_nonneg CHECK ((usd_estimated_total >= (0)::numeric))
);


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    tool_calls jsonb,
    tool_results jsonb,
    tokens_in integer,
    tokens_out integer,
    model character varying(50),
    stop_reason character varying(50),
    action_proposal jsonb,
    meta_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chat_messages_role_valid CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying, 'system'::character varying, 'tool'::character varying])::text[]))),
    CONSTRAINT chat_messages_tokens_in_nonneg CHECK (((tokens_in IS NULL) OR (tokens_in >= 0))),
    CONSTRAINT chat_messages_tokens_out_nonneg CHECK (((tokens_out IS NULL) OR (tokens_out >= 0)))
);


--
-- Name: config_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    parent_version_id uuid,
    sleep_run_id uuid,
    snapshot jsonb NOT NULL,
    risk_class character varying(10) NOT NULL,
    status character varying(20) NOT NULL,
    proposed_at timestamp without time zone DEFAULT now(),
    decided_at timestamp without time zone,
    decided_by uuid,
    applied_at timestamp without time zone,
    q_table_version character varying(30),
    prompt_snapshot text,
    version_name character varying(80),
    CONSTRAINT config_versions_risk_class_valid CHECK (((risk_class)::text = ANY ((ARRAY['bajo'::character varying, 'medio'::character varying, 'alto'::character varying])::text[]))),
    CONSTRAINT config_versions_status_valid CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'applied'::character varying, 'reverted'::character varying])::text[])))
);


--
-- Name: container_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.container_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    action character varying(50) NOT NULL,
    status character varying(20) NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    error text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: episodic_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.episodic_memory (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    state_key character varying(120) NOT NULL,
    action character varying(60) NOT NULL,
    reward numeric(12,6) NOT NULL,
    next_state_key character varying(120),
    order_id uuid,
    consumed_by_sleep_run_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: exchanges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchanges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    code character varying(40) NOT NULL,
    kind character varying(20) DEFAULT 'broker'::character varying NOT NULL,
    meta_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT exchanges_kind_valid CHECK (((kind)::text = ANY ((ARRAY['broker'::character varying, 'exchange'::character varying, 'prop'::character varying, 'demo'::character varying])::text[])))
);


--
-- Name: mfa_recovery_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_recovery_codes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    code_hash character varying(255) NOT NULL,
    used_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: order_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id uuid,
    payload jsonb NOT NULL,
    status character varying(20) NOT NULL,
    requested_at timestamp without time zone DEFAULT now(),
    decided_at timestamp without time zone,
    decided_by uuid,
    expires_at timestamp without time zone NOT NULL,
    CONSTRAINT order_approvals_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'expired'::character varying])::text[])))
);


--
-- Name: order_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    order_id uuid,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    action character varying(50) NOT NULL,
    payload_in jsonb,
    payload_out jsonb,
    risk_check jsonb,
    status character varying(20) NOT NULL,
    error text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT order_log_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'filled'::character varying, 'failed'::character varying, 'blocked'::character varying])::text[])))
);


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    agent_id uuid,
    symbol character varying(20) NOT NULL,
    side character varying(10) NOT NULL,
    volume numeric(10,4) NOT NULL,
    sl numeric(15,5) NOT NULL,
    tp numeric(15,5),
    mt5_ticket bigint,
    status character varying(20) NOT NULL,
    comment character varying(255),
    magic integer,
    created_at timestamp without time zone DEFAULT now(),
    filled_at timestamp without time zone,
    open_time timestamp with time zone,
    open_price numeric(18,8),
    close_time timestamp with time zone,
    close_price numeric(18,8),
    commission numeric(18,4),
    swap numeric(18,4),
    profit_gross numeric(18,4),
    profit_net numeric(18,4),
    meta_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT orders_side_check CHECK (((side)::text = ANY ((ARRAY['buy'::character varying, 'sell'::character varying])::text[]))),
    CONSTRAINT orders_status_valid CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved_pending_send'::character varying, 'filled'::character varying, 'failed'::character varying, 'rejected'::character varying, 'expired'::character varying, 'closed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT orders_volume_check CHECK ((volume > (0)::numeric))
);


--
-- Name: pairs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pairs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    account_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    symbol character varying(20) NOT NULL,
    timeframe character varying(10) NOT NULL,
    status character varying(20) DEFAULT 'inactive'::character varying NOT NULL,
    container_id character varying(100),
    container_name character varying(80),
    docker_image character varying(100) DEFAULT 'mt5-base:latest'::character varying,
    mcp_url character varying(255) NOT NULL,
    mcp_port integer,
    commission_per_lot numeric(10,4),
    commission_currency character varying(10),
    swap_long numeric(10,4),
    swap_short numeric(10,4),
    spread_typical numeric(8,2),
    capital_asignado numeric(15,2),
    risk_per_trade numeric(5,2) DEFAULT 1.0,
    max_daily_dd numeric(5,2) DEFAULT 3.0,
    max_total_dd numeric(5,2) DEFAULT 8.0,
    max_exposure numeric(5,2) DEFAULT 10.0,
    strategy_version integer DEFAULT 1,
    strategy_description text,
    base_logic text,
    orchestrator_agent_id uuid,
    investigator_agent_id uuid,
    marker_agent_id uuid,
    worker_agent_id uuid,
    tutor_agent_id uuid,
    auditor_agent_id uuid,
    trading_sessions text[] DEFAULT '{}'::text[] NOT NULL,
    orchestrator_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    investigator_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    marker_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    worker_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    tutor_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    auditor_params jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    last_active_at timestamp without time zone,
    last_sleep_at timestamp without time zone,
    stopped_at timestamp without time zone,
    tags text[],
    notes text,
    error_count integer DEFAULT 0,
    last_error text,
    CONSTRAINT pairs_trading_sessions_check CHECK ((trading_sessions <@ ARRAY['sydney'::text, 'shanghai'::text, 'tokyo'::text, 'europe'::text, 'new_york'::text]))
);


--
-- Name: q_tables; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.q_tables (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    version integer NOT NULL,
    "table" jsonb NOT NULL,
    alpha_normal numeric(4,3) DEFAULT 0.150 NOT NULL,
    alpha_special numeric(4,3) DEFAULT 0.350 NOT NULL,
    gamma numeric(4,3) DEFAULT 0.920 NOT NULL,
    episode_count integer DEFAULT 0 NOT NULL,
    created_by_sleep_run_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT q_tables_alpha_range CHECK (((alpha_normal >= (0)::numeric) AND (alpha_normal <= (1)::numeric) AND (alpha_special >= (0)::numeric) AND (alpha_special <= (1)::numeric))),
    CONSTRAINT q_tables_episode_count_nonneg CHECK ((episode_count >= 0)),
    CONSTRAINT q_tables_gamma_range CHECK (((gamma >= (0)::numeric) AND (gamma <= (1)::numeric))),
    CONSTRAINT q_tables_version_positive CHECK ((version >= 1))
);


--
-- Name: semantic_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.semantic_memory (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    rule_type character varying(40) NOT NULL,
    body text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    superseded_by uuid,
    active boolean DEFAULT true NOT NULL,
    created_by_sleep_run_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    refresh_token_hash character varying(255) NOT NULL,
    ip_address inet,
    user_agent text,
    issued_at timestamp without time zone DEFAULT now() NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    last_used_at timestamp without time zone DEFAULT now() NOT NULL,
    revoked_at timestamp without time zone,
    CONSTRAINT sessions_expires_after_issued CHECK ((expires_at > issued_at)),
    CONSTRAINT sessions_revoked_after_issued CHECK (((revoked_at IS NULL) OR (revoked_at >= issued_at)))
);


--
-- Name: skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(20) NOT NULL,
    description text,
    version integer DEFAULT 1 NOT NULL,
    code text NOT NULL,
    runtime character varying(20) DEFAULT 'markdown'::character varying NOT NULL,
    input_signature jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_signature jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT skills_runtime_valid CHECK (((runtime)::text = ANY ((ARRAY['markdown'::character varying, 'python'::character varying])::text[]))),
    CONSTRAINT skills_type_valid CHECK (((type)::text = ANY ((ARRAY['indicator'::character varying, 'data_source'::character varying, 'analytic'::character varying, 'executor'::character varying, 'risk'::character varying])::text[])))
);


--
-- Name: sleep_reflections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sleep_reflections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sleep_run_id uuid NOT NULL,
    agent_type character varying(20) NOT NULL,
    reflection_md text,
    suggested_changes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT sleep_reflections_agent_type_valid CHECK (((agent_type)::text = ANY ((ARRAY['orchestrator'::character varying, 'worker'::character varying, 'investigator'::character varying, 'auditor'::character varying])::text[])))
);


--
-- Name: sleep_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sleep_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sleep_run_id uuid NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    summary_md text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: sleep_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sleep_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pair_id uuid NOT NULL,
    user_id uuid NOT NULL,
    phase_type character varying(20) NOT NULL,
    started_at timestamp without time zone DEFAULT now(),
    ended_at timestamp without time zone,
    status character varying(20) NOT NULL,
    summary text,
    error text,
    CONSTRAINT sleep_runs_phase_type_valid CHECK (((phase_type)::text = ANY ((ARRAY['micro'::character varying, 'profundo'::character varying, 'critico'::character varying])::text[]))),
    CONSTRAINT sleep_runs_status_valid CHECK (((status)::text = ANY ((ARRAY['running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'crashed'::character varying, 'skipped'::character varying, 'partial'::character varying])::text[])))
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email character varying(255) NOT NULL,
    display_name character varying(100),
    avatar_url text,
    password_hash character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    email_verified_at timestamp without time zone,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_secret_ref character varying(255),
    last_login_at timestamp without time zone,
    failed_login_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT users_email_lower CHECK (((email)::text = lower((email)::text)))
);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);


--
-- Name: agent_skills agent_skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_pkey PRIMARY KEY (id);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);


--
--


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: chat_action_proposals chat_action_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_action_proposals
    ADD CONSTRAINT chat_action_proposals_pkey PRIMARY KEY (id);


--
-- Name: chat_conversations chat_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: config_versions config_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_pkey PRIMARY KEY (id);


--
-- Name: container_events container_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.container_events
    ADD CONSTRAINT container_events_pkey PRIMARY KEY (id);


--
-- Name: episodic_memory episodic_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodic_memory
    ADD CONSTRAINT episodic_memory_pkey PRIMARY KEY (id);


--
-- Name: exchanges exchanges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchanges
    ADD CONSTRAINT exchanges_pkey PRIMARY KEY (id);


--
-- Name: mfa_recovery_codes mfa_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_pkey PRIMARY KEY (id);


--
-- Name: order_approvals order_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_approvals
    ADD CONSTRAINT order_approvals_pkey PRIMARY KEY (id);


--
-- Name: order_log order_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_log
    ADD CONSTRAINT order_log_pkey PRIMARY KEY (id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: pairs pairs_container_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_container_name_key UNIQUE (container_name);


--
-- Name: pairs pairs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_pkey PRIMARY KEY (id);


--
-- Name: q_tables q_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.q_tables
    ADD CONSTRAINT q_tables_pkey PRIMARY KEY (id);


--
-- Name: semantic_memory semantic_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory
    ADD CONSTRAINT semantic_memory_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_refresh_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: skills skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_pkey PRIMARY KEY (id);


--
-- Name: sleep_reflections sleep_reflections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reflections
    ADD CONSTRAINT sleep_reflections_pkey PRIMARY KEY (id);


--
-- Name: sleep_reports sleep_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reports
    ADD CONSTRAINT sleep_reports_pkey PRIMARY KEY (id);


--
-- Name: sleep_reports sleep_reports_sleep_run_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reports
    ADD CONSTRAINT sleep_reports_sleep_run_id_key UNIQUE (sleep_run_id);


--
-- Name: sleep_runs sleep_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_runs
    ADD CONSTRAINT sleep_runs_pkey PRIMARY KEY (id);


--
-- Name: agent_skills uq_agent_skills_pair; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT uq_agent_skills_pair UNIQUE (agent_id, skill_id);


--
-- Name: exchanges uq_exchanges_user_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchanges
    ADD CONSTRAINT uq_exchanges_user_code UNIQUE (user_id, code);


--
-- Name: sleep_reflections uq_sleep_reflections_run_agent; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reflections
    ADD CONSTRAINT uq_sleep_reflections_run_agent UNIQUE (sleep_run_id, agent_type);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_accounts_exchange; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accounts_exchange ON public.accounts USING btree (exchange_id);


--
-- Name: idx_accounts_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accounts_user ON public.accounts USING btree (user_id);


--
-- Name: idx_agent_runs_agent_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_agent_started ON public.agent_runs USING btree (agent_id, started_at DESC);


--
-- Name: idx_agent_runs_pair_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_pair_started ON public.agent_runs USING btree (pair_id, started_at DESC);


--
-- Name: idx_agent_runs_user_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_user_started ON public.agent_runs USING btree (user_id, started_at DESC);


--
-- Name: idx_agent_skills_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_skills_agent ON public.agent_skills USING btree (agent_id);


--
-- Name: idx_agent_skills_skill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_skills_skill ON public.agent_skills USING btree (skill_id);


--
-- Name: idx_audit_log_action_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_log_action_created ON public.audit_log USING btree (action, created_at DESC);


--
-- Name: idx_audit_log_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_log_user_created ON public.audit_log USING btree (user_id, created_at DESC);


--
-- Name: idx_chat_action_proposals_conv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_action_proposals_conv ON public.chat_action_proposals USING btree (conversation_id, created_at DESC);


--
-- Name: idx_chat_action_proposals_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_action_proposals_status ON public.chat_action_proposals USING btree (pair_id, status) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_chat_conv_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_conv_pair_created ON public.chat_conversations USING btree (pair_id, created_at DESC) WHERE (archived_at IS NULL);


--
-- Name: idx_chat_conv_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_conv_user_created ON public.chat_conversations USING btree (user_id, created_at DESC) WHERE (archived_at IS NULL);


--
-- Name: idx_chat_msg_conv_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_msg_conv_created ON public.chat_messages USING btree (conversation_id, created_at);


--
-- Name: idx_config_versions_pair_proposed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_versions_pair_proposed ON public.config_versions USING btree (pair_id, proposed_at DESC);


--
-- Name: idx_config_versions_sleep_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_versions_sleep_run ON public.config_versions USING btree (sleep_run_id);


--
-- Name: idx_config_versions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_versions_status ON public.config_versions USING btree (status) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_container_events_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_container_events_pair_created ON public.container_events USING btree (pair_id, created_at DESC);


--
-- Name: idx_container_events_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_container_events_user_created ON public.container_events USING btree (user_id, created_at DESC);


--
-- Name: idx_episodic_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_episodic_pair_created ON public.episodic_memory USING btree (pair_id, created_at DESC);


--
-- Name: idx_episodic_pair_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_episodic_pair_state ON public.episodic_memory USING btree (pair_id, state_key);


--
-- Name: idx_episodic_sleep_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_episodic_sleep_run ON public.episodic_memory USING btree (consumed_by_sleep_run_id);


--
-- Name: idx_exchanges_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exchanges_user ON public.exchanges USING btree (user_id);


--
-- Name: idx_mfa_recovery_codes_user_unused; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mfa_recovery_codes_user_unused ON public.mfa_recovery_codes USING btree (user_id) WHERE (used_at IS NULL);


--
-- Name: idx_order_approvals_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_order_approvals_pair_created ON public.order_approvals USING btree (pair_id, requested_at DESC);


--
-- Name: idx_order_log_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_order_log_pair_created ON public.order_log USING btree (pair_id, created_at DESC);


--
-- Name: idx_orders_mt5_ticket; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_orders_mt5_ticket ON public.orders USING btree (mt5_ticket) WHERE (mt5_ticket IS NOT NULL);


--
-- Name: idx_orders_pair_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_pair_created ON public.orders USING btree (pair_id, created_at DESC);


--
-- Name: idx_orders_pair_open_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_pair_open_time ON public.orders USING btree (pair_id, open_time DESC);


--
-- Name: idx_orders_pair_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_pair_status ON public.orders USING btree (pair_id, status);


--
-- Name: idx_orders_pair_symbol; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_pair_symbol ON public.orders USING btree (pair_id, symbol);


--
-- Name: idx_pairs_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pairs_account ON public.pairs USING btree (account_id);


--
-- Name: idx_pairs_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pairs_user ON public.pairs USING btree (user_id);


--
-- Name: idx_q_tables_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_q_tables_pair ON public.q_tables USING btree (pair_id);


--
-- Name: idx_q_tables_pair_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_q_tables_pair_created_at ON public.q_tables USING btree (pair_id, created_at DESC);


--
-- Name: idx_semantic_pair_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_pair_active ON public.semantic_memory USING btree (pair_id, active);


--
-- Name: idx_semantic_pair_rule_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_pair_rule_type ON public.semantic_memory USING btree (pair_id, rule_type);


--
-- Name: idx_sessions_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_token_hash ON public.sessions USING btree (refresh_token_hash) WHERE (revoked_at IS NULL);


--
-- Name: idx_sessions_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_user_active ON public.sessions USING btree (user_id) WHERE (revoked_at IS NULL);


--
-- Name: idx_skills_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_skills_user_active ON public.skills USING btree (user_id) WHERE (is_active = true);


--
-- Name: idx_sleep_reflections_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sleep_reflections_run ON public.sleep_reflections USING btree (sleep_run_id);


--
-- Name: idx_sleep_reports_sleep_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sleep_reports_sleep_run ON public.sleep_reports USING btree (sleep_run_id);


--
-- Name: idx_sleep_runs_pair_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sleep_runs_pair_started ON public.sleep_runs USING btree (pair_id, started_at DESC);


--
-- Name: idx_sleep_runs_status_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sleep_runs_status_started ON public.sleep_runs USING btree (status, started_at);


--
-- Name: idx_sleep_runs_user_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sleep_runs_user_started ON public.sleep_runs USING btree (user_id, started_at DESC);


--
-- Name: idx_users_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_active ON public.users USING btree (id) WHERE (is_active = true);


--
-- Name: uq_mfa_recovery_codes_user_id_code_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_mfa_recovery_codes_user_id_code_hash ON public.mfa_recovery_codes USING btree (user_id, code_hash);


--
-- Name: uq_q_tables_pair_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_q_tables_pair_version ON public.q_tables USING btree (pair_id, version);


--
-- Name: accounts accounts_exchange_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_exchange_id_fkey FOREIGN KEY (exchange_id) REFERENCES public.exchanges(id) ON DELETE RESTRICT;


--
-- Name: accounts accounts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: agent_runs agent_runs_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: agent_runs agent_runs_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: agent_runs agent_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: agent_skills agent_skills_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE CASCADE;


--
-- Name: agent_skills agent_skills_skill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_skills
    ADD CONSTRAINT agent_skills_skill_id_fkey FOREIGN KEY (skill_id) REFERENCES public.skills(id) ON DELETE RESTRICT;


--
-- Name: agents agents_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: audit_log audit_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: chat_action_proposals chat_action_proposals_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_action_proposals
    ADD CONSTRAINT chat_action_proposals_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id) ON DELETE CASCADE;


--
-- Name: chat_action_proposals chat_action_proposals_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_action_proposals
    ADD CONSTRAINT chat_action_proposals_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: chat_action_proposals chat_action_proposals_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_action_proposals
    ADD CONSTRAINT chat_action_proposals_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;


--
-- Name: chat_action_proposals chat_action_proposals_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_action_proposals
    ADD CONSTRAINT chat_action_proposals_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE CASCADE;


--
-- Name: chat_conversations chat_conversations_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE CASCADE;


--
-- Name: chat_conversations chat_conversations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_conversations
    ADD CONSTRAINT chat_conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: chat_messages chat_messages_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.chat_conversations(id) ON DELETE CASCADE;


--
-- Name: config_versions config_versions_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: config_versions config_versions_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: config_versions config_versions_parent_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_parent_version_id_fkey FOREIGN KEY (parent_version_id) REFERENCES public.config_versions(id);


--
-- Name: config_versions config_versions_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_versions
    ADD CONSTRAINT config_versions_sleep_run_id_fkey FOREIGN KEY (sleep_run_id) REFERENCES public.sleep_runs(id);


--
-- Name: container_events container_events_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.container_events
    ADD CONSTRAINT container_events_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: container_events container_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.container_events
    ADD CONSTRAINT container_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: episodic_memory episodic_memory_consumed_by_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodic_memory
    ADD CONSTRAINT episodic_memory_consumed_by_sleep_run_id_fkey FOREIGN KEY (consumed_by_sleep_run_id) REFERENCES public.sleep_runs(id) ON DELETE SET NULL;


--
-- Name: episodic_memory episodic_memory_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodic_memory
    ADD CONSTRAINT episodic_memory_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE SET NULL;


--
-- Name: episodic_memory episodic_memory_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.episodic_memory
    ADD CONSTRAINT episodic_memory_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE CASCADE;


--
-- Name: exchanges exchanges_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchanges
    ADD CONSTRAINT exchanges_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: mfa_recovery_codes mfa_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_recovery_codes
    ADD CONSTRAINT mfa_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: order_approvals order_approvals_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_approvals
    ADD CONSTRAINT order_approvals_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE SET NULL;


--
-- Name: order_approvals order_approvals_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_approvals
    ADD CONSTRAINT order_approvals_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: order_approvals order_approvals_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_approvals
    ADD CONSTRAINT order_approvals_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: order_approvals order_approvals_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_approvals
    ADD CONSTRAINT order_approvals_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: order_log order_log_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_log
    ADD CONSTRAINT order_log_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id) ON DELETE CASCADE;


--
-- Name: order_log order_log_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_log
    ADD CONSTRAINT order_log_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: order_log order_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_log
    ADD CONSTRAINT order_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: orders orders_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.agents(id) ON DELETE SET NULL;


--
-- Name: orders orders_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: orders orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_auditor_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_auditor_agent_id_fkey FOREIGN KEY (auditor_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_investigator_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_investigator_agent_id_fkey FOREIGN KEY (investigator_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_marker_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_marker_agent_id_fkey FOREIGN KEY (marker_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_orchestrator_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_orchestrator_agent_id_fkey FOREIGN KEY (orchestrator_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_tutor_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_tutor_agent_id_fkey FOREIGN KEY (tutor_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: pairs pairs_worker_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pairs
    ADD CONSTRAINT pairs_worker_agent_id_fkey FOREIGN KEY (worker_agent_id) REFERENCES public.agents(id) ON DELETE RESTRICT;


--
-- Name: q_tables q_tables_created_by_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.q_tables
    ADD CONSTRAINT q_tables_created_by_sleep_run_id_fkey FOREIGN KEY (created_by_sleep_run_id) REFERENCES public.sleep_runs(id) ON DELETE SET NULL;


--
-- Name: q_tables q_tables_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.q_tables
    ADD CONSTRAINT q_tables_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE CASCADE;


--
-- Name: semantic_memory semantic_memory_created_by_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory
    ADD CONSTRAINT semantic_memory_created_by_sleep_run_id_fkey FOREIGN KEY (created_by_sleep_run_id) REFERENCES public.sleep_runs(id) ON DELETE SET NULL;


--
-- Name: semantic_memory semantic_memory_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory
    ADD CONSTRAINT semantic_memory_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE CASCADE;


--
-- Name: semantic_memory semantic_memory_superseded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory
    ADD CONSTRAINT semantic_memory_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES public.semantic_memory(id) ON DELETE SET NULL;


--
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: skills skills_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT skills_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: sleep_reflections sleep_reflections_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reflections
    ADD CONSTRAINT sleep_reflections_sleep_run_id_fkey FOREIGN KEY (sleep_run_id) REFERENCES public.sleep_runs(id) ON DELETE CASCADE;


--
-- Name: sleep_reports sleep_reports_sleep_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_reports
    ADD CONSTRAINT sleep_reports_sleep_run_id_fkey FOREIGN KEY (sleep_run_id) REFERENCES public.sleep_runs(id) ON DELETE CASCADE;


--
-- Name: sleep_runs sleep_runs_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_runs
    ADD CONSTRAINT sleep_runs_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.pairs(id) ON DELETE RESTRICT;


--
-- Name: sleep_runs sleep_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sleep_runs
    ADD CONSTRAINT sleep_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
--

\unrestrict Fu43JgXFw1fpUFFBZf8BQ3kGCNKbx6SXqf1taOUjks8uomJIFkgghUEgNJKg5Gb

