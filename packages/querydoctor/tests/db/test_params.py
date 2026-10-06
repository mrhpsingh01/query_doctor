import datetime as dt
import decimal

import pytest

from querydoctor.db import ParamMismatch
from querydoctor.db.explain import check_params


def test_accepts_json_scalars_and_common_python_scalars():
    check_params([1, 2.5, "x", True, None, decimal.Decimal("1.5"), dt.date(2024, 1, 1), dt.datetime(2024, 1, 1)])


@pytest.mark.parametrize("bad", [{"a": 1}, [1, 2], object(), b"bytes"])
def test_rejects_containers_and_exotic_types(bad):
    with pytest.raises(ParamMismatch):
        check_params([bad])
