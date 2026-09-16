from dataclasses import dataclass, field

from preauth.domain.enums import ActorType, ReviewerRole


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated party performing an operation.

    Identity is established upstream (API gateway / IAM). This object only carries the asserted identity.
    """

    type: ActorType
    id: str
    roles: frozenset[ReviewerRole] = field(default_factory=frozenset)

    def has_role(self, role: ReviewerRole) -> bool:
        return role in self.roles


SYSTEM_ACTOR = Actor(type=ActorType.SYSTEM, id="preauth-system")
