"""Errors."""





class ConfigurationError(ValueError):
    code = "invalid_configuration"

    def __init__(self, message, *, fields=()):
        super().__init__(message)
        self.fields = list(fields)



class ConfigurationConflict(ConfigurationError):
    code = "configuration_revision_conflict"



class ConfigurationRequired(ConfigurationError):
    code = "configuration_required"



class ProviderConfigurationChanged(ConfigurationError):
    code = "provider_configuration_changed"
