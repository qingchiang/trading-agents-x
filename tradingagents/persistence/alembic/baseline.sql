-- Independent runtime baseline. Changes require a new migration revision.
CREATE TABLE application_configuration (
	id INTEGER NOT NULL,
	values_json JSON NOT NULL,
	initialized BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE configuration_credentials (
	name VARCHAR(100) NOT NULL,
	value TEXT NOT NULL,
	PRIMARY KEY (name)
);

CREATE TABLE "decisions" (
	id INTEGER NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	ticker VARCHAR(64) NOT NULL,
	market VARCHAR(80),
	asset_type VARCHAR(20) NOT NULL,
	analysis_date DATE NOT NULL,
	rating VARCHAR(20) NOT NULL,
	confidence VARCHAR(10) NOT NULL,
	decision_json JSON NOT NULL,
	numeric_audit_json JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
	UNIQUE (run_id)
);

CREATE TABLE model_connections (
	id VARCHAR(64) NOT NULL,
	definition JSON NOT NULL,
	legacy_provider VARCHAR(80),
	PRIMARY KEY (id),
	UNIQUE (legacy_provider)
);

CREATE TABLE primary_research_cycles (
	instrument VARCHAR(64) NOT NULL,
	full_run_id VARCHAR(36) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (instrument),
	FOREIGN KEY(full_run_id) REFERENCES research_nodes (run_id) ON DELETE CASCADE
);

CREATE TABLE research_nodes (
	run_id VARCHAR(36) NOT NULL,
	research_kind VARCHAR(20) NOT NULL,
	full_baseline_run_id VARCHAR(36),
	created_at DATETIME NOT NULL, incremental_products_json JSON,
	PRIMARY KEY (run_id),
	FOREIGN KEY(full_baseline_run_id) REFERENCES runs (id) ON DELETE RESTRICT,
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE TABLE run_artifacts (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	attempt INTEGER NOT NULL,
	stage VARCHAR(80) NOT NULL,
	role VARCHAR(80) NOT NULL,
	round INTEGER NOT NULL,
	schema_version VARCHAR(20) NOT NULL,
	prompt_version VARCHAR(80) NOT NULL,
	generation_method VARCHAR(40) NOT NULL,
	content_type VARCHAR(40) NOT NULL,
	content_json JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL, generation_observations_json JSON,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
	CONSTRAINT uq_run_artifact_identity UNIQUE (run_id, stage, role, round, prompt_version)
);

CREATE TABLE run_attempts (
	id INTEGER NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	attempt INTEGER NOT NULL,
	status VARCHAR(20) NOT NULL,
	checkpoint_thread_id VARCHAR(200) NOT NULL,
	resume_count INTEGER NOT NULL,
	lease_owner VARCHAR(120),
	lease_expires_at DATETIME,
	started_at DATETIME,
	finished_at DATETIME,
	error_code VARCHAR(80),
	error_message TEXT,
	metrics_json JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
	UNIQUE (run_id, attempt)
);

CREATE TABLE run_events (
	id INTEGER NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	sequence INTEGER NOT NULL,
	attempt INTEGER NOT NULL,
	event_type VARCHAR(100) NOT NULL,
	node VARCHAR(160),
	payload_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE,
	UNIQUE (run_id, sequence)
);

CREATE TABLE run_evidence (
	run_id VARCHAR(36) NOT NULL,
	sealed_attempt INTEGER NOT NULL,
	bundle_json JSON NOT NULL,
	digest VARCHAR(64) NOT NULL,
	item_count INTEGER NOT NULL,
	table_count INTEGER NOT NULL,
	sealed_at DATETIME NOT NULL,
	PRIMARY KEY (run_id),
	FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE CASCADE
);

CREATE TABLE runs (
	id VARCHAR(36) NOT NULL,
	source_run_id VARCHAR(36),
	idempotency_key VARCHAR(200),
	status VARCHAR(20) NOT NULL,
	instrument_name VARCHAR(300),
	request_json JSON NOT NULL,
	config_json JSON NOT NULL,
	version VARCHAR(40) NOT NULL,
	current_attempt INTEGER NOT NULL,
	cancel_requested BOOLEAN NOT NULL,
	lease_owner VARCHAR(120),
	lease_expires_at DATETIME,
	error_code VARCHAR(80),
	error_message TEXT,
	metrics_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	started_at DATETIME,
	finished_at DATETIME,
	trashed_at DATETIME,
	updated_at DATETIME NOT NULL, instrument_local_name VARCHAR(300), research_schema_version VARCHAR(20), information_cutoff_at DATETIME, method_snapshot_json JSON, research_kind VARCHAR(20), full_baseline_run_id VARCHAR(36), incremental_cutoff DATE, incremental_input_fingerprint VARCHAR(64), trash_cascade_full_run_id VARCHAR(36), submission_json JSON,
	PRIMARY KEY (id),
	FOREIGN KEY(source_run_id) REFERENCES runs (id) ON DELETE SET NULL,
	UNIQUE (idempotency_key)
);

CREATE INDEX ix_decisions_market ON decisions (market);

CREATE UNIQUE INDEX ix_decisions_run_id ON decisions (run_id);

CREATE INDEX ix_decisions_ticker ON decisions (ticker);

CREATE INDEX ix_run_artifacts_order ON run_artifacts (run_id, attempt, created_at);

CREATE INDEX ix_run_artifacts_run_id ON run_artifacts (run_id);

CREATE INDEX ix_run_attempts_run_id ON run_attempts (run_id);

CREATE INDEX ix_run_events_replay ON run_events (run_id, sequence);

CREATE INDEX ix_run_events_run_id ON run_events (run_id);

CREATE INDEX ix_runs_claim ON runs (status, lease_expires_at, created_at);

CREATE INDEX ix_runs_status ON runs (status);

CREATE INDEX ix_runs_trash ON runs (trashed_at, created_at);

CREATE INDEX ix_runs_trash_cascade_full_run_id ON runs (trash_cascade_full_run_id);

CREATE UNIQUE INDEX uq_active_incremental_cycle_cutoff ON runs (full_baseline_run_id, incremental_cutoff) WHERE research_kind = 'incremental' AND trashed_at IS NULL AND status IN ('queued', 'running', 'succeeded');
