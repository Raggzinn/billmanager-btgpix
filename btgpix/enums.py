"""BTG Pactual collection status enumerations."""

from enum import Enum
from typing import FrozenSet


class CollectionStatus(str, Enum):
    """Possible status values for a BTG Pactual instant collection.

    Reference:
        https://developers.empresas.btgpactual.com/reference/get_collections
    """

    CREATED = "CREATED"        # Collection created, not yet active
    PROCESSING = "PROCESSING"  # Being processed by BTG
    ACTIVE = "ACTIVE"          # QR code active, awaiting payment
    PAID = "PAID"              # Payment received
    OVERDUE = "OVERDUE"        # Past due date
    CANCELED = "CANCELED"      # Canceled by user or system
    FAILED = "FAILED"          # Processing failed


#: Statuses that indicate the collection lifecycle is complete.
TERMINAL_STATUSES: FrozenSet[CollectionStatus] = frozenset({
    CollectionStatus.PAID,
    CollectionStatus.OVERDUE,
    CollectionStatus.CANCELED,
    CollectionStatus.FAILED,
})

#: Statuses that indicate the collection is still awaiting payment.
PENDING_STATUSES: FrozenSet[CollectionStatus] = frozenset({
    CollectionStatus.CREATED,
    CollectionStatus.PROCESSING,
    CollectionStatus.ACTIVE,
})
