"""Model role selections independent of connection transport and execution."""

from pydantic import Field

from tradingagents.domain.common import FrozenModel


class ModelSelection(FrozenModel):
    connection_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    reasoning_effort: str | None = Field(default=None, min_length=1)

    def inherit(self, defaults: 'ModelSelection') -> 'ModelSelection':
        return ModelSelection.model_validate({
            field: getattr(self, field) if getattr(self, field) is not None else getattr(defaults, field)
            for field in type(self).model_fields
        })


class RoleSelections(FrozenModel):
    quick: ModelSelection | None = None
    deep: ModelSelection | None = None
