import json
import logging
import re
import random
import uuid

from functools import reduce

from Fill import FillError
from Hints import lineWrap, gossipLocations
from Item import ItemFactory
from ItemPool import random_choices, item_groups, rewardlist, get_junk_item
from LocationList import location_table, location_groups
from Spoiler import HASH_ICONS
from State import State
from version import __version__
from Utils import random_choices


per_world_keys = (
    'dungeons',
    'trials',
    'item_pool',
    'starting_items',
    'locations',
    ':woth_locations',
    ':barren_regions',
    'gossip',
)


starting_always_extra = {
    **{reward: True for reward in rewardlist},
    "Deku Sticks": True,
    "Deku Nuts": True,
    "Bombs": True,
    "Arrows": True,
    "Deku Seeds": True,
    "Bombchus": True,
    "Magic Bean": True,
    "Bottle with Milk (Half)": True,
    "Rupees": True,
}


locations_always_extra = {
    **{reward: False for reward in rewardlist},
}


# The inner tuple format is (Offset, Value, Or with existing bits?)

def bottle_writes(bottle_id, maximum=4):
    def get_writes(count, state):
        if count > maximum:
            return None
        next_bottle_offset = state.get('next_bottle_offset', 0x86)
        if next_bottle_offset + count > 0x8A:
            return None
        state['next_bottle_offset'] = next_bottle_offset + count
        return ((next_bottle_offset + i, bottle_id, False) for i in range(count))
    return get_writes

def piece_of_heart_writes(count, state):
    pieces_of_heart = state.get('pieces_of_heart', 12) + count
    if pieces_of_heart > 80:
        return None
    state['pieces_of_heart'] = pieces_of_heart
    heart_container_value = (pieces_of_heart // 4) * 0x10
    return ((0x2E, (heart_container_value & 0xFF00) >> 8, False), (0x2F, heart_container_value & 0xFF, False), (0x30, (heart_container_value & 0xFF00) >> 8, False), (0x31, heart_container_value & 0xFF, False), (0xA4, (pieces_of_heart % 4) << 4, False))

def heart_container_writes(count, state):
    pieces_of_heart = state.get('pieces_of_heart', 12) + count * 4
    if pieces_of_heart > 80:
        return None
    state['pieces_of_heart'] = pieces_of_heart
    heart_container_value = (pieces_of_heart // 4) * 0x10
    return ((0x2E, (heart_container_value & 0xFF00) >> 8, False), (0x2F, heart_container_value & 0xFF, False), (0x30, (heart_container_value & 0xFF00) >> 8, False), (0x31, heart_container_value & 0xFF, False))

save_writes_table = {
    "Deku Stick Capacity": (((0x74, 0x00, False), (0xA1, 0x04, True)),
                            ((0x74, 0x00, False), (0xA1, 0x06, True))),
    "Deku Sticks": lambda n, state: ((0x8C, n, False),) if state['distribution'].get_starting_item('Deku Stick Capacity').count > 0 else ((0x74, 0x00, False), (0x8C, n, False), (0xA1, 0x02, True)),
    "Deku Nut Capacity": (((0x75, 0x01, False), (0xA1, 0x20, True)),
                          ((0x75, 0x01, False), (0xA1, 0x30, True))),
    "Deku Nuts": lambda n, state: ((0x8D, n, False),) if state['distribution'].get_starting_item('Deku Nut Capacity').count > 0 else ((0x75, 0x01, False), (0x8D, n, False), (0xA1, 0x10, True)),
    "Bomb Bag": (((0x76, 0x02, False), (0xA2, 0x40, True)),
                 ((0x76, 0x02, False), (0xA2, 0x80, True)),
                 ((0x76, 0x02, False), (0xA2, 0xC0, True))),
    "Bombs": lambda n, state: ((0x76, 0x02, False), (0x8E, n, False)),
    "Bow": (((0x77, 0x03, False), (0xA3, 0x01, True)),
            ((0x77, 0x03, False), (0xA3, 0x02, True)),
            ((0x77, 0x03, False), (0xA3, 0x03, True))),
    "Arrows": lambda n, state: ((0x8F, n, False),),
    "Fire Arrows": (((0x78, 0x04, False),),),
    "Dins Fire": (((0x79, 0x05, False),),),
    "Slingshot": (((0x7A, 0x06, False), (0xA3, 0x08, True)),
                  ((0x7A, 0x06, False), (0xA3, 0x10, True)),
                  ((0x7A, 0x06, False), (0xA3, 0x18, True))),
    "Deku Seeds": lambda n, state: ((0x7A, 0x06, False), (0x92, n, False)),
    "Ocarina": (((0x7B, 0x07, False),),
                ((0x7B, 0x08, False),)),
    "Bombchus": lambda n, state: ((0x7C, 0x09, False), (0x94, n, False)),
    "Progressive Hookshot": (((0x7D, 0x0A, False),),
                             ((0x7D, 0x0B, False),)),
    "Ice Arrows": (((0x7E, 0x0C, False),),),
    "Farores Wind": (((0x7F, 0x0D, False),),),
    "Boomerang": (((0x80, 0x0E, False),),),
    "Lens of Truth": (((0x81, 0x0F, False),),),
    "Magic Bean": lambda n, state: ((0x82, 0x10, False), (0x9A, n, False), (0x9B, n, False)) if n <= 10 else None,
    "Hammer": (((0x83, 0x11, False),),),
    "Light Arrows": (((0x84, 0x12, False),),),
    "Nayrus Love": (((0x85, 0x13, False),),),

    "Bottle": bottle_writes(0x14),
    "Bottle with Red Potion": bottle_writes(0x15),
    "Bottle with Green Potion": bottle_writes(0x16),
    "Bottle with Blue Potion": bottle_writes(0x17),
    "Bottle with Fairy": bottle_writes(0x18),
    "Bottle with Fish": bottle_writes(0x19),
    "Bottle with Milk": bottle_writes(0x1A),
    "Bottle with Letter": bottle_writes(0x1B, maximum=1),
    "Bottle with Blue Fire": bottle_writes(0x1C),
    "Bottle with Bugs": bottle_writes(0x1D),
    "Bottle with Big Poe": bottle_writes(0x1E),
    "Bottle with Milk (Half)": bottle_writes(0x1F), # This one is not in the item list, so it needs a special case
    "Bottle with Poe": bottle_writes(0x20),

    "Pocket Egg": (((0x8A, 0x2D, False),),),
    "Pocket Cucco": (((0x8A, 0x2E, False),),),
    "Cojiro": (((0x8A, 0x2F, False),),),
    "Odd Mushroom": (((0x8A, 0x30, False),),),
    "Poachers Saw": (((0x8A, 0x32, False),),),
    "Broken Sword": (((0x8A, 0x33, False),),),
    "Prescription": (((0x8A, 0x34, False),),),
    "Eyeball Frog": (((0x8A, 0x35, False),),),
    "Eyedrops": (((0x8A, 0x36, False),),),
    "Claim Check": (((0x8A, 0x37, False),),),

    "Weird Egg": (((0x8B, 0x21, False),),),
    "Chicken": (((0x8B, 0x22, False),),),

    "Goron Tunic": (((0x9C, 0x02, True),),),
    "Zora Tunic": (((0x9C, 0x04, True),),),
    "Iron Boots": (((0x9C, 0x20, True),),),
    "Hover Boots": (((0x9C, 0x40, True),),),
    "Deku Shield": (((0x71, 0x10, True), (0x9D, 0x10, True)),),
    "Hylian Shield": (((0x9D, 0x20, True),),),
    "Mirror Shield": (((0x53, 0x40, True), (0x9D, 0x40, True)),),
    "Kokiri Sword": (((0x68, 0x3B, False), (0x71, 0x01, True), (0x9D, 0x01, True)),),
    "Biggoron Sword": (((0x3E, 0x01, False), (0x9D, 0x04, True)),),

    "Gerudo Membership Card": (((0xA5, 0x40, True),),),
    "Stone of Agony": (((0xA5, 0x20, True),),),

    "Zeldas Lullaby": (((0xA6, 0x10, True),),),
    "Eponas Song": (((0xA6, 0x20, True),),),
    "Sarias Song": (((0xA6, 0x40, True),),),
    "Suns Song": (((0xA6, 0x80, True),),),
    "Song of Time": (((0xA5, 0x01, True),),),
    "Song of Storms": (((0xA5, 0x02, True),),),
    "Minuet of Forest": (((0xA7, 0x40, True),),),
    "Bolero of Fire": (((0xA7, 0x80, True),),),
    "Serenade of Water": (((0xA6, 0x01, True),),),
    "Requiem of Spirit": (((0xA6, 0x02, True),),),
    "Nocturne of Shadow": (((0xA6, 0x04, True),),),
    "Prelude of Light": (((0xA6, 0x08, True),),),

    "Kokiri Emerald": (((0xA5, 0x04, True),),),
    "Goron Ruby": (((0xA5, 0x08, True),),),
    "Zora Sapphire": (((0xA5, 0x10, True),),),
    "Light Medallion": (((0xA7, 0x20, True),),),
    "Forest Medallion": (((0xA7, 0x01, True),),),
    "Fire Medallion": (((0xA7, 0x02, True),),),
    "Water Medallion": (((0xA7, 0x04, True),),),
    "Spirit Medallion": (((0xA7, 0x08, True),),),
    "Shadow Medallion": (((0xA7, 0x10, True),),),

    "Progressive Strength Upgrade": (((0xA3, 0x40, True),),
                                     ((0xA3, 0x80, True),),
                                     ((0xA3, 0xC0, True),)),
    "Progressive Scale": (((0xA2, 0x02, True),),
                          ((0xA2, 0x04, True),)),
    "Progressive Wallet": (((0xA2, 0x10, True),),
                           ((0xA2, 0x20, True),),
                           ((0xA2, 0x30, True),)),

    "Gold Skulltula Token": lambda n, state: ((0xA5, 0x80, True), (0xD0, n, False)),

    "Double Defense": (((0x3D, 0x01, False), (0xCF, 0x14, False)),),
    "Magic Meter": (((0x32, 0x01, False), (0x33, 0x30, False), (0x3A, 0x01, False)),
                    ((0x32, 0x01, False), (0x33, 0x60, False), (0x3A, 0x01, False), (0x3C, 0x01, False))),

    "Piece of Heart": piece_of_heart_writes,
    "Piece of Heart (Treasure Chest Game)": piece_of_heart_writes,
    "Heart Container": heart_container_writes,

    "Rupees": lambda n, state: ((0x34, (n & 0xFF00) >> 8, False), (0x35, n & 0xFF, False))
}


def SimpleRecord(props):
    class Record(object):
        def __init__(self, src_dict=None):
            self.processed = False
            self.update(src_dict, update_all=True)


        def update(self, src_dict, update_all=False):
            if src_dict is None:
                src_dict = {}
            for k, p in props.items():
                if update_all or k in src_dict:
                    setattr(self, k, src_dict.get(k, p))


        def to_dict(self):
            return {k: getattr(self, k) for (k, d) in props.items() if getattr(self, k) != d}


        def __str__(self):
            return to_json(self.to_dict())
    return Record


class DungeonRecord(SimpleRecord({'mq': None})):
    pass


class GossipRecord(SimpleRecord({'gossip': None})):
    pass


class ItemPoolRecord(SimpleRecord({'add': None, 'remove': None, 'set': None})):
    def update(self, src_dict, update_all=False):
        super().update(src_dict, update_all)
        if list(map(lambda key: getattr(self, key), ['add', 'remove', 'set'])).count(None) != 2:
            raise ValueError("One of 'add', 'remove', or 'set' must be set in a ItemPoolRecord.")


class LocationRecord(SimpleRecord({'item': None, 'player': None, 'price': None, 'model': None, 'extra': None})):
    @staticmethod
    def from_item(item):
        return LocationRecord({
            'item': item.name,
            'player': None if item.location is not None and item.world is item.location.world else (item.world.id + 1),
            'model': item.looks_like_item.name if item.looks_like_item is not None and item.location.has_preview() and can_cloak(item, item.looks_like_item) else None,
            'price': item.price,
        })


class LogicIgnoredItemRecord(SimpleRecord({'count': 1})):
    pass


class StarterRecord(SimpleRecord({'count': 1, 'extra': None})):
    pass


class TrialRecord(SimpleRecord({'skip': None})):
    pass


class WorldDistribution(object):
    def __init__(self, distribution, id, src_dict={}):
        self.distribution = distribution
        self.id = id
        self.base_pool = []
        self.update(src_dict, update_all=True)


    def update(self, src_dict, update_all=False):
        update_dict = {        
            'dungeons': {name: DungeonRecord(record) for (name, record) in src_dict.get('dungeons', {}).items()},
            'trials': {name: TrialRecord(record) for (name, record) in src_dict.get('trials', {}).items()},
            'item_pool': {name: ItemPoolRecord(record) for (name, record) in src_dict.get('item_pool', {}).items()},
            'starting_items': {name: StarterRecord(record) for (name, record) in src_dict.get('starting_items', {}).items()},
            'locations': {name: [LocationRecord(rec) for rec in record] if is_pattern(name) else LocationRecord(record) for (name, record) in src_dict.get('locations', {}).items() if not is_output_only(name)},
            'woth_locations': None,
            'barren_regions': None,
            'gossip': {name: [GossipRecord(rec) for rec in record] if is_pattern(name) else GossipRecord(record) for (name, record) in src_dict.get('gossip', {}).items()},
        }

        if update_all:
            self.__dict__.update(update_dict)
        else:
            for k in src_dict:
                if k in update_dict:
                    value = update_dict[k]
                    if self.__dict__.get(k, None) is None:
                        setattr(self, k, value)
                    elif isinstance(value, dict):
                        getattr(self, k).update(value)
                    elif isinstance(value, list):
                        getattr(self, k).extend(value)
                    else:
                        setattr(self, k, None)


    def to_dict(self):
        return {
            'dungeons': {name: record.to_dict() for (name, record) in self.dungeons.items()},
            'trials': {name: record.to_dict() for (name, record) in self.trials.items()},
            'item_pool': {name: record.to_dict() for (name, record) in self.item_pool.items()},
            'starting_items': {name: record.to_dict() for (name, record) in self.starting_items.items()},
            'locations': {name: [rec.to_dict() for rec in record] if is_pattern(name) else record.to_dict() for (name, record) in self.locations.items()},
            ':woth_locations': None if self.woth_locations is None else {name: record.to_dict() for (name, record) in self.woth_locations.items()},
            ':barren_regions': self.barren_regions,
            'gossip': {name: [rec.to_dict() for rec in record] if is_pattern(name) else record.to_dict() for (name, record) in self.gossip.items()},
        }


    def __str__(self):
        return to_json(self.to_dict())


    def configure_dungeons(self, world, dungeon_pool):
        dist_num_mq = 0
        for (name, record) in self.dungeons.items():
            if record.mq is not None:
                dungeon_pool.remove(name)
                if record.mq:
                    dist_num_mq += 1
                    world.dungeon_mq[name] = True
        return dist_num_mq


    def configure_trials(self, trial_pool):
        dist_chosen = []
        for (name, record) in self.trials.items():
            if record.skip is not None:
                trial_pool.remove(name)
                if not record.skip:
                    dist_chosen.append(name)
        return dist_chosen


    def get_all_item_replacements(self):
        yield from self.item_replacements
        add_items = []
        remove_items = []
        for (_, record) in pattern_dict_items(self.locations):
            if coalesce(locations_always_extra.get(record.item), record.extra, self.distribution.locations_default_extra):
                add_items.append(record.item)
        for (name, record) in self.starting_items.items():
            if not coalesce(starting_always_extra.get(name), record.extra, self.distribution.starting_default_extra):
                remove_items.extend([name] * record.count)
        if len(remove_items) > len(add_items):
            add_items.extend(['#Junk'] * (len(remove_items) - len(add_items)))
        elif len(add_items) > len(remove_items):
            remove_items.extend(['#Junk'] * (len(add_items) - len(remove_items)))
        yield from (ItemReplacementRecord({ 'add': add_item, 'remove': remove_item }) for (add_item, remove_item) in zip(add_items, remove_items))


    def pool_remove_item(self, pools, item_name, count, replace_bottle=False, world_id=None, use_base_pool=True):
        removed_items = []

        if is_pattern(item_name):
            base_remove_matcher = pattern_matcher(item_name, item_groups)
        else:
            base_remove_matcher = lambda item: item_name == item
        remove_matcher = lambda item: base_remove_matcher(item) and ((item in self.base_pool) ^ (not use_base_pool))

        if world_id is None:
            predicate = remove_matcher
        else:
            predicate = lambda item: item.world.id == world_id and remove_matcher(item.name)

        for i in range(count):
            removed_item = pull_random_element(pools, predicate)
            if removed_item is None:
                if not use_base_pool:
                    raise KeyError('No items matching "%s" or all of them have already been removed' % (item_name))
                else:
                    removed_items.extend(self.pool_remove_item(pools, item_name, count - i, world_id=world_id, use_base_pool=False))
                    break
            if use_base_pool:
                if world_id is None:
                    self.base_pool.remove(removed_item)
                else:
                    self.base_pool.remove(removed_item.name)
            removed_items.append(removed_item)

        for item in removed_items:
            if replace_bottle:
                bottle_matcher = pattern_matcher("#Bottle", item_groups)
                trade_matcher  = pattern_matcher("#AdultTrade", item_groups)
                if bottle_matcher(item):
                    self.pool_add_item(pools[0], "#Bottle", 1)
                if trade_matcher(item):
                    self.pool_add_item(pools[0], "#AdultTrade", 1)

        return removed_items


    def pool_add_item(self, pool, item_name, count, replace_bottle=False):
        added_items = []
        if item_name == '#Junk':
            added_items = get_junk_item(count)
        elif is_pattern(item_name):
            add_matcher = pattern_matcher(item_name, item_groups)
            candidates = [item for item in pool if add_matcher(item)]
            added_items = random_choices(candidates, k=count)
        else:
            added_items = [item_name] * count

        for item in added_items:
            if replace_bottle:
                bottle_matcher = pattern_matcher("#Bottle", item_groups)
                trade_matcher  = pattern_matcher("#AdultTrade", item_groups)
                if bottle_matcher(item):
                    self.pool_remove_item([pool], "#Bottle", 1)
                if trade_matcher(item):
                    self.pool_remove_item([pool], "#AdultTrade", 1)
            pool.append(item)


    def alter_pool(self, world, pool):
        self.base_pool = list(pool)
        pool_size = len(pool)

        for item_name, record in self.item_pool.items():
            if record.add is not None:
                self.pool_add_item(pool, item_name, record.add)
            if record.remove is not None:
                self.pool_remove_item([pool], item_name, record.remove)

        for item_name, record in self.item_pool.items():
            if record.set is not None:
                if item_name == '#Junk':
                    raise ValueError('#Junk item group cannot have a set number of items')
                elif is_pattern(item_name):
                    predicate = pattern_matcher(item_name, item_groups)
                else:
                    predicate = lambda item: item_name == item
                pool_match = [item for item in pool if predicate(item)]
                add_count = record.set - len(pool_match)
                if add_count > 0:
                    self.pool_add_item(pool, item_name, add_count, replace_bottle=True)
                else:
                    self.pool_remove_item([pool], item_name, -add_count, replace_bottle=True)

        junk_to_add = pool_size - len(pool)
        if junk_to_add > 0:
            self.pool_add_item(pool, "#Junk", junk_to_add)
        else:
            self.pool_remove_item([pool], "#Junk", -junk_to_add)


    def collect_starters(self, state):
        for (name, record) in self.starting_items.items():
            for _ in range(record.count):
                try:
                    item = ItemFactory("Bottle" if name == "Bottle with Milk (Half)" else name)
                except KeyError:
                    continue
                state.collect(item)


    def fill_bosses(self, world, prize_locs, prizepool):
        count = 0
        for (name, record) in pattern_dict_items(self.locations):
            boss = pull_item_or_location([prize_locs], world, name, groups=location_groups)
            if boss is None:
                continue
            if record.player is not None and (record.player - 1) != self.id:
                raise RuntimeError('A boss can only give rewards in its own world')
            reward = pull_item_or_location([prizepool], world, record.item, groups=item_groups)
            if reward is None:
                raise RuntimeError('Reward unknown or already placed in world %d: %s' % (world.id + 1, record.item))
            count += 1
            world.push_item(boss, reward, True)
            record.processed = True
        return count


    def fill(self, window, worlds, location_pools, item_pools):
        world = worlds[self.id]
        for (name, record) in pattern_dict_items(self.locations):
            if record.processed:
                continue
            if record.item is None:
                continue

            player_id = self.id if record.player is None else record.player - 1

            location = pull_item_or_location(location_pools, world, name, groups=location_groups)
            if location is None:
                raise RuntimeError('Location unknown or already filled in world %d: %s' % (self.id + 1, name))

            try:
                item = self.pool_remove_item(item_pools, record.item, 1, world_id=player_id)[0]
            except KeyError:
                try:
                    self.pool_remove_item(item_pools, "#Junk", 1, world_id=player_id)[0]
                    item = ItemFactory(record.item, worlds[player_id])
                except KeyError:
                    raise RuntimeError('Too many items were added to world %d, and not enough junk is available to be removed.' % (self.id + 1))

            if record.price is not None:
                item.price = record.price
            location.world.push_item(location, item, True)
            if item.advancement:
                states_after = State.get_states_with_items([world.state for world in worlds], reduce(lambda a, b: a + b, item_pools))
                if not State.can_beat_game(states_after, True):
                    raise FillError('%s in world %d is not reachable without %s in world %d!' % (location.name, self.id + 1, item.name, player_world.id + 1))
            window.fillcount += 1
            window.update_progress(5 + ((window.fillcount / window.locationcount) * 30))


    def cloak(self, world, location_pools, model_pools):
        for (name, record) in pattern_dict_items(self.locations):
            if record.processed:
                continue
            if record.model is None:
                continue
            location = pull_item_or_location(location_pools, world, name, groups=location_groups)
            if location is None:
                raise RuntimeError('Location unknown or already cloaked in world %d: %s' % (self.id + 1, name))
            model = pull_item_or_location(model_pools, world, record.model, remove=False, groups=item_groups)
            if model is None:
                raise RuntimeError('Unknown model in world %d: %s' % (self.id + 1, record.model))
            if can_cloak(location.item, model):
                location.item.looks_like_item = model


    def configure_gossip(self, spoiler, stoneIDs):
        for (name, record) in pattern_dict_items(self.gossip):
            if is_pattern(name):
                matcher = pattern_matcher(name)
                stoneID = pull_random_element([stoneIDs], lambda id: matcher(gossipLocations[id].name))
            else:
                stoneID = pull_first_element([stoneIDs], lambda id: gossipLocations[id].name == name)
            if stoneID is None:
                raise RuntimeError('Gossip stone unknown or already assigned in world %d: %s' % (self.id + 1, name))
            spoiler.hints[self.id][stoneID] = lineWrap(record.gossip)


    def get_starting_item(self, name):
        if name in self.starting_items:
            return self.starting_items[name]
        return StarterRecord({ 'count': 0 })


    def patch_save(self, write_byte_to_save, write_bits_to_save):
        bytes_to_write = {}
        bits_to_write = {}
        state = {
            'distribution': self
        }
        for (name, record) in self.starting_items.items():
            if record.count == 0:
                continue
            if name not in save_writes_table:
                raise RuntimeError('Unknown starting item: %s' % (name))
            save_writes = save_writes_table[name]
            if callable(save_writes):
                writes = save_writes(record.count, state)
            else:
                writes = save_writes[min(record.count, len(save_writes)) - 1]
            if writes is None:
                raise RuntimeError('Unsupported value for starting item %s: %d' % (name, record.count))
            for (offset, value, is_bits) in writes:
                if is_bits:
                    if offset in bytes_to_write:
                        bytes_to_write[offset] |= value
                    else:
                        bits_to_write[offset] = bits_to_write.get(offset, 0) | value
                else:
                    if offset in bits_to_write:
                        del bits_to_write[offset]
                    bytes_to_write[offset] = value
        for (offset, value) in bytes_to_write.items():
            write_byte_to_save(offset, value)
        for (offset, value) in bits_to_write.items():
            write_bits_to_save(offset, value)


class Distribution(object):
    def __init__(self, settings, src_dict={}):
        self.settings = settings
        self.worlds = [WorldDistribution(self, id) for id in range(settings.world_count)]
        self.update(src_dict, update_all=True)


    def for_world(self, world_id):
        while world_id >= self.settings.world_count:
            raise RuntimeError("World ID %d is outside of the range of worlds" % (world_id + 1))
        return self.worlds[world_id]


    def fill(self, window, worlds, location_pools, item_pools):
        states_before = State.get_states_with_items([world.state for world in worlds], reduce(lambda a, b: a + b, item_pools))
        if not State.can_beat_game(states_before, True):
            raise FillError('Item pool does not contain items required to beat game!')

        for world_dist in self.worlds:
            world_dist.fill(window, worlds, location_pools, item_pools)


    def cloak(self, worlds, location_pools, model_pools):
        for world_dist in self.worlds:
            world_dist.cloak(worlds[world_dist.id], location_pools, model_pools)


    def update(self, src_dict, update_all=False):
        update_dict = {        
            'file_hash': (src_dict.get('file_hash', []) + [None, None, None, None, None])[0:5],
            'locations_default_extra': src_dict.get('locations_default_extra', False),
            'starting_default_extra': src_dict.get('starting_default_extra', True),
            'playthrough': None,
        }

        if update_all:
            self.__dict__.update(update_dict)
            for world in self.worlds:
                world.update({}, update_all=True)
        else:
            for k in src_dict:
                setattr(self, k, update_dict[k])

        for k in per_world_keys:
            if k in src_dict:
                for world_id, world in enumerate(self.worlds):
                    world_key = 'World %d' % (world_id + 1)
                    if world_key in src_dict[k]:
                        world.update({k: src_dict[k][world_key]})
                        del src_dict[k][world_key]
                for world in self.worlds:
                    if src_dict[k]:
                        world.update({k: src_dict[k]})


    def to_dict(self, include_output=True):
        self_dict = {
            ':version': __version__,
            ':seed': self.settings.seed if self.settings is not None else None,
            'file_hash': self.file_hash,
            ':settings_string': self.settings.settings_string if self.settings is not None else None,
            ':settings': self.settings.to_dict() if self.settings is not None else None,
            ':distribution': self.settings.distribution.to_dict(False) if include_output and self.settings is not None else None,
            ':playthrough': None if self.playthrough is None else 
                {sphere_nr: {name: record.to_dict() for name, record in sphere.items()} 
                    for (sphere_nr, sphere) in self.playthrough.items()},
        }

        worlds = [world.to_dict() for world in self.worlds]
        if self.settings.world_count > 1:
            self_dict.update({k: {'World %d' % (id + 1): world[k] for id, world in enumerate(worlds)} for k in per_world_keys})
        else:
            self_dict.update({k: worlds[0][k] for k in per_world_keys})

        if self.locations_default_extra:
            self_dict['locations_default_extra'] = True
        if not self.starting_default_extra:
            self_dict['starting_default_extra'] = False
        if not include_output:
            strip_output_only(self_dict)
        return self_dict


    def to_str(self, include_output_only=True):
        return to_json(self.to_dict(include_output_only))


    def __str__(self):
        return to_json(self.to_dict())


    @staticmethod
    def from_spoiler(spoiler):
        dist = Distribution(spoiler.settings)
        dist.file_hash = [HASH_ICONS[icon] for icon in spoiler.file_hash]

        for world in spoiler.worlds:
            world_dist = dist.for_world(world.id)
            src_dist = world.get_distribution()
            world_dist.dungeons = {dung: DungeonRecord({ 'mq': world.dungeon_mq[dung] }) for dung in world.dungeon_mq}
            world_dist.trials = {trial: TrialRecord({ 'skip': world.skipped_trials[trial] }) for trial in world.skipped_trials}
            world_dist.item_pool = {}
            world_dist.starting_items = {name: StarterRecord({ 'count': record.count }) for (name, record) in src_dist.starting_items.items()}
            world_dist.locations = {loc: LocationRecord.from_item(item) for (loc, item) in spoiler.locations[world.id].items()}
            world_dist.woth_locations = {loc.name: LocationRecord.from_item(loc.item) for loc in spoiler.required_locations[world.id]}
            world_dist.barren_regions = [*world.empty_areas]
            world_dist.gossip = {gossipLocations[loc].name: GossipRecord({ 'gossip': spoiler.hints[world.id][loc] }) for loc in spoiler.hints[world.id]}

        for world in spoiler.worlds:
            for (_, item) in spoiler.locations[world.id].items():
                if item.dungeonitem or item.type == 'Event':
                    continue
                player_dist = dist.for_world(item.world.id)
                if item.name in player_dist.item_pool:
                    player_dist.item_pool[item.name].set += 1
                else:
                    player_dist.item_pool[item.name] = ItemPoolRecord({'set':1})

        dist.playthrough = {}
        for (sphere_nr, sphere) in spoiler.playthrough.items():
            loc_rec_sphere = {}
            dist.playthrough[sphere_nr] = loc_rec_sphere
            for location in sphere:
                if spoiler.settings.world_count > 1:
                    location_key = '%s [W%d]' % (location.name, location.world.id + 1)
                else:
                    location_key = location.name

                loc_rec_sphere[location_key] = LocationRecord.from_item(location.item)

        return dist


    @staticmethod
    def from_file(settings, filename):
        with open(filename) as infile:
            src_dict = json.load(infile)
        return Distribution(settings, src_dict)


    def to_file(self, filename):
        json = str(self)
        with open(filename, 'w') as outfile:
            outfile.write(json)


def strip_output_only(obj):
    if isinstance(obj, list):
        for elem in obj:
            strip_output_only(elem)
    elif isinstance(obj, dict):
        output_only_keys = [key for key in obj if is_output_only(key)]
        for key in output_only_keys:
            del obj[key]
        for elem in obj.values():
            strip_output_only(elem)


JSON_ARRAY_OR_DICT_OF_SCALARS_OR_STRING = re.compile(r'\{([^"\{\}\[\]]|"([^"\\]|\\.)*")*\}|\[([^"\{\}\[\]]|"([^"\\]|\\.)*")*\]|"([^"\\]|\\.)*"')
def to_json(obj):
    # Serialize objects/arrays that contain other objects/arrays on many lines with indent
    # But serialize object/arrays that only contain scalars or are empty on a single line
    def deindent(m):
        indented = m.group(0)
        if indented.startswith('"'):
            return indented
        raw = json.dumps(json.loads(m.group(0)))
        if len(raw) == 2:
            return '%s %s' % (raw[0], raw[-1])
        else:
            return '%s %s %s' % (raw[0], raw[1:-1], raw[-1])
    return re.sub(JSON_ARRAY_OR_DICT_OF_SCALARS_OR_STRING, deindent, json.dumps(obj, indent='\t'))


def coalesce(*values):
    for value in values:
        if value is not None:
            return value
    return None


def can_cloak(actual_item, model):
    return actual_item.index == 0x7C # Ice Trap


def is_output_only(pattern):
    return pattern.startswith(':')


def is_pattern(pattern):
    return pattern.startswith('!') or pattern.startswith('*') or pattern.startswith('#') or pattern.endswith('*')


def pattern_matcher(pattern, groups={}):
    invert = pattern.startswith('!')
    if invert:
        pattern = pattern[1:]
    if pattern.startswith('#'):
        group = groups[pattern[1:]]
        return lambda s: invert != (s in group)
    wildcard_begin = pattern.startswith('*')
    if wildcard_begin:
        pattern = pattern[1:]
    wildcard_end = pattern.endswith('*')
    if wildcard_end:
        pattern = pattern[:-1]
        if wildcard_begin:
            return lambda s: invert != (pattern in s)
        else:
            return lambda s: invert != s.startswith(pattern)
    else:
        if wildcard_begin:
            return lambda s: invert != s.endswith(pattern)
        else:
            return lambda s: invert != (s == pattern)


def pattern_dict_items(pattern_dict):
    for (key, value) in pattern_dict.items():
        if is_pattern(key):
            for subvalue in value:
                yield (key, subvalue)
        else:
            yield (key, value)


def pull_element(pools, predicate=lambda k:True, first=True, remove=True):
    return pull_first_element(pools, predicate, remove) if first else pull_random_element(pools, predicate, remove)


def pull_first_element(pools, predicate=lambda k:True, remove=True):
    for pool in pools:
        for element in pool:
            if predicate(element):
                if remove:
                    pool.remove(element)
                return element
    return None


def pull_random_element(pools, predicate=lambda k:True, remove=True):
    candidates = [(element, pool) for pool in pools for element in pool if predicate(element)]
    if len(candidates) == 0:
        return None
    element, pool = random.choice(candidates)
    if remove:
        pool.remove(element)
    return element


# Finds and removes (unless told not to do so) an item or location matching the criteria from a list of pools.
def pull_item_or_location(pools, world, name, remove=True, groups={}):
    if is_pattern(name):
        matcher = pattern_matcher(name, groups)
        return pull_random_element(pools, lambda e: e.world is world and matcher(e.name), remove)
    else:
        return pull_first_element(pools, lambda e: e.world is world and e.name == name, remove)