"""FastAPI dependency providers."""

from typing import Annotated

from fastapi import Depends

from alos.config import Settings, get_settings

SettingsDependency = Annotated[Settings, Depends(get_settings)]
