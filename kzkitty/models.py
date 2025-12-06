import os
from enum import StrEnum

from tortoise import Model, Tortoise, fields

class Type(StrEnum):
    TP = 'TP'
    PRO = 'PRO'
    ANY = 'any'

class Mode(StrEnum):
    KZT = 'KZT'
    SKZ = 'SKZ'
    VNL = 'VNL'

class Map(Model):
    name = fields.CharField(max_length=255, primary_key=True)
    tier = fields.IntField()
    vnl_tier = fields.IntField(null=True)
    vnl_pro_tier = fields.IntField(null=True)
    thumbnail = fields.BinaryField(null=True)

class Player(Model):
    user_id = fields.IntField()
    server_id = fields.IntField()
    steamid64 = fields.IntField(null=True)
    mode = fields.CharEnumField(Mode, default=Mode.KZT)

    class Meta: # type: ignore
        unique_together = ('user_id', 'server_id')

async def init_db() -> None:
    await Tortoise.init(
        db_url=f"sqlite://{os.environ['KZKITTY_DB']}",
        modules={'models': ['kzkitty.models']},
    )
    await Tortoise.generate_schemas()

close_db = Tortoise.close_connections
