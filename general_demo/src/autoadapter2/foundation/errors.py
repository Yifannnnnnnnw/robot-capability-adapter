class AutoAdapterError(Exception):
    pass


class IdentifierError(AutoAdapterError, ValueError):
    pass


class IntegrityError(AutoAdapterError):
    pass


class ImmutableError(IntegrityError):
    pass


class ContractError(AutoAdapterError, ValueError):
    pass


class ReferenceResolutionError(ContractError):
    pass


class SchemaValidationError(ContractError):
    pass


class VisibilityError(ContractError):
    pass


class ArtifactNotFoundError(AutoAdapterError, FileNotFoundError):
    pass


class StateTransitionError(ContractError):
    pass
