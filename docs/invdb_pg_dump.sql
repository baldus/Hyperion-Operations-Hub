-- Extract from legacy PostgreSQL schema (pg_dump -s)

CREATE TABLE public.production_chart_settings (
    id integer NOT NULL,
    primary_min numeric(10,2),
    primary_max numeric(10,2),
    primary_step numeric(10,2),
    secondary_min numeric(10,2),
    secondary_max numeric(10,2),
    secondary_step numeric(10,2),
    goal_value numeric(10,2),
    show_goal boolean DEFAULT false NOT NULL
);

CREATE TABLE public.production_daily_record (
    id integer NOT NULL,
    entry_date date NOT NULL,
    day_of_week character varying(16) NOT NULL,
    controllers_4_stop integer DEFAULT 0 NOT NULL,
    controllers_6_stop integer DEFAULT 0 NOT NULL,
    door_locks_lh integer DEFAULT 0 NOT NULL,
    door_locks_rh integer DEFAULT 0 NOT NULL,
    operators_produced integer DEFAULT 0 NOT NULL,
    cops_produced integer DEFAULT 0 NOT NULL,
    daily_notes text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

CREATE TABLE public.item (
    id integer NOT NULL,
    sku character varying NOT NULL,
    name character varying NOT NULL,
    unit character varying DEFAULT 'ea'::character varying,
    description character varying,
    min_stock integer DEFAULT 0
);

CREATE TABLE public.batch (
    id integer NOT NULL,
    item_id integer NOT NULL,
    lot_number character varying,
    quantity integer DEFAULT 0,
    received_date timestamp without time zone NOT NULL
);

CREATE TABLE public."order" (
    id integer NOT NULL,
    order_number character varying NOT NULL,
    status character varying DEFAULT 'SCHEDULED'::character varying NOT NULL,
    promised_date date,
    scheduled_start_date date,
    scheduled_completion_date date,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);
