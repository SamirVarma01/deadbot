class UserFacingError(Exception):
    """An expected failure whose message is safe and useful to show in Discord."""


class NotLinkedError(UserFacingError):
    pass


class NotSyncedError(UserFacingError):
    pass


class TooFewMatchesError(UserFacingError):
    pass
