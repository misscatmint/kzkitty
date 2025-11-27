import os
from enum import StrEnum

from tortoise import Model, Tortoise, fields

class Mode(StrEnum):
    KZT = 'kzt'
    SKZ = 'skz'
    VNL = 'vnl'

class Map(Model):
    name = fields.CharField(max_length=255, primary_key=True)
    tier = fields.IntField()
    vnl_tier = fields.IntField(null=True)
    vnl_pro_tier = fields.IntField(null=True)
    thumbnail = fields.BinaryField(null=True)

class Player(Model):
    id = fields.IntField(primary_key=True)
    steamid64 = fields.IntField(null=True)
    mode = fields.CharEnumField(Mode, default=Mode.KZT)

async def init_db() -> None:
    await Tortoise.init(
        db_url=f"sqlite://{os.environ['KZKITTY_DB']}",
        modules={'models': ['kzkitty.models']},
    )
    await Tortoise.generate_schemas()

close_db = Tortoise.close_connections
