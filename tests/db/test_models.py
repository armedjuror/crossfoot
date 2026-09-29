from crossfoot.config import get_settings
from crossfoot.db import get_session
from crossfoot.db.models import Tenant


def test_default_tenant_exists():
    with get_session(get_settings().database_url) as session:
        tenant = session.query(Tenant).filter_by(name="default").one()
        assert tenant.id is not None
