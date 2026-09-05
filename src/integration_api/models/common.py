from typing import Annotated

from pydantic import Field, StrictInt

VSwitchId = Annotated[StrictInt, Field(ge=1, le=65535)]
PortId = Annotated[StrictInt, Field(ge=0, le=65535)]
NonNegativeIdentity = Annotated[StrictInt, Field(ge=0)]
