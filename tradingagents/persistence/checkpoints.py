"""Keep provider exception credentials out of durable graph error writes."""

from langgraph.checkpoint.sqlite import SqliteSaver

from tradingagents.credentials import credential_redactor


class CredentialSafeSqliteSaver(SqliteSaver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Saver background writes may outlive the invoking thread's context.
        self._redact_credentials = credential_redactor()

    def put_writes(self, config, writes, task_id, task_path=""):
        safe_writes = [
            (
                channel,
                self._redact_credentials(repr(value))
                if isinstance(value, BaseException)
                else value,
            )
            for channel, value in writes
        ]
        return super().put_writes(config, safe_writes, task_id, task_path)
