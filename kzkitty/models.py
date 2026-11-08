import csv
import logging
import os
import sys
from enum import StrEnum
from typing import TYPE_CHECKING

from tortoise import Model, Tortoise, fields
if TYPE_CHECKING:
    from kzkitty.api.a2s import ServerData

_logger = logging.getLogger('kzkitty.models')

class Type(StrEnum):
    TP = 'TP'
    PRO = 'PRO'
    ANY = 'any'

class Mode(StrEnum):
    KZT = 'KZT'
    SKZ = 'SKZ'
    VNL = 'VNL'
    CKZ = 'CKZ'
    VNL2 = 'VNL (CS2)'

class Map(Model):
    name = fields.CharField(max_length=255)
    map_id = fields.IntField()
    is_cs2 = fields.BooleanField()
    tier = fields.IntField(null=True)
    pro_tier = fields.IntField(null=True)
    vnl_tier = fields.IntField(null=True)
    vnl_pro_tier = fields.IntField(null=True)
    skz_possible = fields.BooleanField(null=True)

class Course(Model):
    name = fields.CharField(max_length=255)
    course_id = fields.IntField()
    map_id = fields.IntField()
    tier = fields.IntField(null=True)
    pro_tier = fields.IntField(null=True)
    vnl_tier = fields.IntField(null=True)
    vnl_pro_tier = fields.IntField(null=True)

    class Meta: # pyright: ignore # pyrefly: ignore # ty: ignore
        unique_together = ('course_id', 'map_id')

class Player(Model):
    user_id: fields.Field[int] = fields.BigIntField()
    server_id: fields.Field[int] = fields.BigIntField()
    steamid64: fields.Field[int] = fields.BigIntField()
    mode = fields.CharEnumField(Mode, default=Mode.KZT)

    class Meta: # pyright: ignore # pyrefly: ignore # ty: ignore
        unique_together = ('user_id', 'server_id')

class Game(StrEnum):
    CSGO = 'CSGO'
    CS2 = 'CS2'

class ServerMessage(Model):
    channel_id: fields.Field[int] = fields.BigIntField()
    message_id: fields.Field[int | None] = fields.BigIntField(null=True)

class Server(Model):
    name = fields.CharField(max_length=255)
    address = fields.CharField(max_length=255)
    game = fields.CharEnumField(Game)
    preferred_mode = fields.CharEnumField(Mode, null=True)
    location = fields.CharField(max_length=255)
    server_id: fields.Field[int] = fields.BigIntField()
    status_message: fields.ForeignKeyNullableRelation[ServerMessage] = (
        fields.ForeignKeyField('models.ServerMessage', related_name='servers',
                               null=True, on_delete=fields.SET_NULL))
    is_default = fields.BooleanField(default=False)
    failure_count = fields.IntField(default=0)
    last_query: fields.JSONField[ServerData | None] = (
        fields.JSONField(null=True))

    class Meta: # pyright: ignore # pyrefly: ignore # ty: ignore
        unique_together = ('address', 'server_id')

async def init_db(db_url: str) -> None:
    await Tortoise.init(db_url=db_url, modules={'models': ['kzkitty.models']},
                        _enable_global_fallback=True)
    await Tortoise.generate_schemas()

close_db = Tortoise.close_connections

async def _import_default_players() -> None:
    default_player_file = os.environ.get('KZKITTY_DEFAULT_PLAYERS')
    if default_player_file is None:
        return

    users: list[Player] = []
    with open(default_player_file, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            user_id = int(row['user_id'])
            server_id = int(row['server_id'])
            if not await Player.exists(user_id=user_id, server_id=server_id):
                users.append(Player(user_id=user_id,
                                    server_id=server_id,
                                    steamid64=int(row['steamid64']),
                                    mode=Mode(row['mode'])))
    await Player.bulk_create(users)
    if users:
        _logger.info('Imported %d player(s) from %s', len(users),
                     default_player_file)

async def _status_message_for_channel(channel_id: int, message_id: int | None
                                      ) -> ServerMessage:
    db_message = await ServerMessage.filter(channel_id=channel_id).first()
    if db_message is None:
        return await ServerMessage.create(channel_id=channel_id,
                                          message_id=message_id)
    if message_id is None or message_id == db_message.message_id:
        return db_message
    if db_message.message_id is not None:
        raise ValueError(f'Conflicting message IDs for channel {channel_id}')
    db_message.message_id = message_id
    await db_message.save(update_fields=['message_id'])
    return db_message

async def _import_default_servers() -> None:
    default_server_file = os.environ.get('KZKITTY_DEFAULT_SERVERS')
    if default_server_file is None:
        return

    servers: list[Server] = []
    with open(default_server_file, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            name = row['name']
            address = row['address']
            location = row['location']
            game = row['game']
            preferred_mode = row['preferred_mode'] or None
            server_id = int(row['server_id'])
            channel_id = int(row['channel_id']) if row['channel_id'] else None
            message_id = int(row['message_id']) if row['message_id'] else None
            is_default = bool(int(row['is_default']))
            if not await Server.exists(address=address, server_id=server_id):
                status_message = (
                    await _status_message_for_channel(channel_id, message_id)
                    if channel_id is not None else None)
                servers.append(Server(name=name, address=address,
                                      location=location, game=game,
                                      preferred_mode=preferred_mode,
                                      server_id=server_id,
                                      status_message=status_message,
                                      is_default=is_default))
    await Server.bulk_create(servers)
    if servers:
        _logger.info('Imported %d server(s) from %s', len(servers),
                     default_server_file)

async def import_defaults() -> None:
    await _import_default_players()
    await _import_default_servers()

async def export_default_players() -> None:
    default_player_file = os.environ['KZKITTY_DEFAULT_PLAYERS']
    with open(default_player_file, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        if reader.fieldnames is None:
            raise ValueError('Default players CSV missing header')
        writer = csv.DictWriter(sys.stdout, fieldnames=reader.fieldnames,
                                lineterminator=os.linesep)
        writer.writeheader()
        for row in reader:
            user_id = int(row['user_id'])
            server_id = int(row['server_id'])
            p = await Player.get(user_id=user_id, server_id=server_id)
            row['steamid64'] = p.steamid64
            row['mode'] = p.mode
            writer.writerow(row)

async def dump_players() -> None:
    fields = ['user_id', 'server_id', 'steamid64', 'mode']
    writer = csv.DictWriter(sys.stdout, fieldnames=fields,
                            lineterminator=os.linesep)
    writer.writeheader()
    for p in await Player.all():
        row = {'user_id': p.user_id, 'server_id': p.server_id,
               'steamid64': p.steamid64, 'mode': p.mode}
        writer.writerow(row)
