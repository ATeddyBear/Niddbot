#!/usr/bin/env python

import argparse
import asyncio
import json
import os
import shutil
import sys
import tarfile
from copy import deepcopy
from datetime import datetime as dt
from pathlib import Path

import appdirs
from redbot.core.cli import confirm
from redbot.core.data_manager import basic_config_default
from redbot.core.json_io import JsonIO
from redbot.core.utils import safe_delete
from redbot.core.drivers.red_json import JSON

config_dir = None
appdir = appdirs.AppDirs("Red-DiscordBot")
if sys.platform == 'linux':
    if 0 < os.getuid() < 1000:
        config_dir = Path(appdir.site_data_dir)
if not config_dir:
    config_dir = Path(appdir.user_config_dir)
try:
    config_dir.mkdir(parents=True, exist_ok=True)
except PermissionError:
    print(
        "You don't have permission to write to "
        "'{}'\nExiting...".format(config_dir))
    sys.exit(1)
config_file = config_dir / 'config.json'


def parse_cli_args():
    parser = argparse.ArgumentParser(
        description="Red - Discord Bot's instance manager (V3)"
    )
    parser.add_argument(
        "--delete", "-d",
        help="Interactively delete an instance",
        action="store_true"
    )
    parser.add_argument(
        "--edit", "-e",
        help="Interactively edit an instance",
        action="store_true"
    )
    return parser.parse_known_args()


def load_existing_config():
    if not config_file.exists():
        return {}

    return JsonIO(config_file)._load_json()


def save_config(name, data, remove=False):
    config = load_existing_config()
    if remove and name in config:
        config.pop(name)
    else:
        if name in config:
            print(
                "WARNING: An instance already exists with this name. "
                "Continuing will overwrite the existing instance config."
            )
            if not confirm("Are you absolutely certain you want to continue (y/n)? "):
                print("Not continuing")
                sys.exit(0)
        config[name] = data
    JsonIO(config_file)._save_json(config)


def get_data_dir():
    default_data_dir = Path(appdir.user_data_dir)

    print("Hello! Before we begin the full configuration process we need to"
          " gather some initial information about where you'd like us"
          " to store your bot's data. We've attempted to figure out a"
          " sane default data location which is printed below. If you don't"
          " want to change this default please press [ENTER], otherwise"
          " input your desired data location.")
    print()
    print("Default: {}".format(default_data_dir))

    new_path = input('> ')

    if new_path != '':
        new_path = Path(new_path)
        default_data_dir = new_path

    if not default_data_dir.exists():
        try:
            default_data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            print("We were unable to create your chosen directory."
                  " You may need to restart this process with admin"
                  " privileges.")
            sys.exit(1)

    print("You have chosen {} to be your data directory."
          "".format(default_data_dir))
    if not confirm("Please confirm (y/n):"):
        print("Please start the process over.")
        sys.exit(0)
    return default_data_dir


def get_storage_type():
    storage_dict = {
        1: "JSON",
        2: "MongoDB"
    }
    storage = None
    while storage is None:
        print()
        print("Please choose your storage backend (if you're unsure, choose 1).")
        print("1. JSON (file storage, requires no database).")
        print("2. MongoDB")
        storage = input("> ")
        try:
            storage = int(storage)
        except ValueError:
            storage = None
        else:
            if storage not in storage_dict:
                storage = None
    return storage


def get_name():
    name = ""
    while len(name) == 0:
        print()
        print("Please enter a name for your instance, this name cannot include spaces"
              " and it will be used to run your bot from here on out.")
        name = input("> ")
        if " " in name:
            name = ""
    return name


def basic_setup():
    """
    Creates the data storage folder.
    :return:
    """

    default_data_dir = get_data_dir()

    default_dirs = deepcopy(basic_config_default)
    default_dirs['DATA_PATH'] = str(default_data_dir.resolve())

    storage = get_storage_type()

    storage_dict = {
        1: "JSON",
        2: "MongoDB"
    }
    default_dirs['STORAGE_TYPE'] = storage_dict.get(storage, 1)

    if storage_dict.get(storage, 1) == "MongoDB":
        from redbot.core.drivers.red_mongo import get_config_details
        default_dirs['STORAGE_DETAILS'] = get_config_details()
    else:
        default_dirs['STORAGE_DETAILS'] = {}

    name = get_name()
    save_config(name, default_dirs)

    print()
    print("Your basic configuration has been saved. Please run `redbot <name>` to"
          " continue your setup process and to run the bot.")


async def json_to_mongo(current_data_dir: Path, storage_details: dict):
    from redbot.core.drivers.red_mongo import Mongo
    core_data_file = list(current_data_dir.glob("core/settings.json"))[0]
    m = Mongo("Core", "0", **storage_details)
    with core_data_file.open(mode="r") as f:
        core_data = json.loads(f.read())
    collection = m.get_collection()
    await collection.update_one(
        {'_id': m.unique_cog_identifier},
        update={"$set": core_data["0"]},
        upsert=True
    )
    for p in current_data_dir.glob("cogs/**/settings.json"):
        with p.open(mode="r") as f:
            cog_data = json.loads(f.read())
        cog_i = None
        for ident in list(cog_data.keys()):
            cog_i = str(hash(ident))
        cog_m = Mongo(p.parent.stem, cog_i, **storage_details)
        cog_c = cog_m.get_collection()
        for ident in list(cog_data.keys()):
            await cog_c.update_one(
                {"_id": cog_m.unique_cog_identifier},
                update={"$set": cog_data[cog_i]},
                upsert=True
            )


async def mongo_to_json(current_data_dir: Path, storage_details: dict):
    from redbot.core.drivers.red_mongo import Mongo
    m = Mongo("Core", "0", **storage_details)
    db = m.db
    collection_names = await db.collection_names(include_system_collections=False)
    for c_name in collection_names:
        if c_name == "Core":
            c_data_path = current_data_dir / "core"
        else:
            c_data_path = current_data_dir / "cogs/{}".format(c_name)
        output = {}
        docs = await db[c_name].find().to_list(None)
        c_id = None
        for item in docs:
            item_id = item.pop("_id")
            if not c_id:
                c_id = str(hash(item_id))
            output[item_id] = item
        target = JSON(c_name, c_id, data_path_override=c_data_path)
        await target.jsonIO._threadsafe_save_json(output)


async def edit_instance():
    instance_list = load_existing_config()
    if not instance_list:
        print("No instances have been set up!")
        return

    print(
        "You have chosen to edit an instance. The following "
        "is a list of instances that currently exist:\n"
    )
    for instance in instance_list.keys():
        print("{}\n".format(instance))
    print("Please select one of the above by entering its name")
    selected = input("> ")

    if selected not in instance_list.keys():
        print("That isn't a valid instance!")
        return
    instance_data = instance_list[selected]
    default_dirs = deepcopy(basic_config_default)

    current_data_dir = Path(instance_data["DATA_PATH"])
    print(
        "You have selected '{}' as the instance to modify.".format(selected)
    )
    if not confirm("Please confirm (y/n):"):
        print("Ok, we will not continue then.")
        return

    print("Ok, we will continue on.")
    print()
    if confirm("Would you like to change the instance name? (y/n)"):
        name = get_name()
    else:
        name = selected

    if confirm("Would you like to change the data location? (y/n)"):
        default_data_dir = get_data_dir()
        default_dirs["DATA_PATH"] = str(default_data_dir.resolve())
    else:
        default_dirs["DATA_PATH"] = str(current_data_dir.resolve())

    if confirm("Would you like to change the storage type? (y/n):"):
        storage = get_storage_type()

        storage_dict = {
            1: "JSON",
            2: "MongoDB"
        }
        default_dirs["STORAGE_TYPE"] = storage_dict[storage]
        if storage_dict.get(storage, 1) == "MongoDB":
            from redbot.core.drivers.red_mongo import get_config_details
            storage_details = get_config_details()
            default_dirs["STORAGE_DETAILS"] = storage_details

            if instance_data["STORAGE_TYPE"] == "JSON":
                if confirm("Would you like to import your data? (y/n) "):
                    await json_to_mongo(current_data_dir, storage_details)
        else:
            storage_details = instance_data["STORAGE_DETAILS"]
            default_dirs["STORAGE_DETAILS"] = {}
            if instance_data["STORAGE_TYPE"] == "MongoDB":
                if confirm("Would you like to import your data? (y/n) "):
                    await mongo_to_json(current_data_dir, storage_details)

    if name != selected:
        save_config(selected, {}, remove=True)
    save_config(name, default_dirs)

    print(
        "Your basic configuration has been edited"
    )


async def create_backup(selected, instance_data):
    if confirm("Would you like to make a backup of the data for this instance? (y/n)"):
        if instance_data["STORAGE_TYPE"] == "MongoDB":
            print("Backing up the instance's data...")
            await mongo_to_json(instance_data["DATA_PATH"], instance_data["STORAGE_DETAILS"])
            backup_filename = "redv3-{}-{}.tar.gz".format(
                selected, dt.utcnow().strftime("%Y-%m-%d %H-%M-%S")
            )
            pth = Path(instance_data["DATA_PATH"])
            if pth.exists():
                home = pth.home()
                backup_file = home / backup_filename
                os.chdir(str(pth.parent))
                with tarfile.open(str(backup_file), "w:gz") as tar:
                    tar.add(pth.stem)
                print("A backup of {} has been made. It is at {}".format(
                    selected, backup_file
                ))
                
        else:
            print("Backing up the instance's data...")
            backup_filename = "redv3-{}-{}.tar.gz".format(
                selected, dt.utcnow().strftime("%Y-%m-%d %H-%M-%S")
            )
            pth = Path(instance_data["DATA_PATH"])
            if pth.exists():
                home = pth.home()
                backup_file = home / backup_filename
                os.chdir(str(pth.parent))  # str is used here because 3.5 support
                with tarfile.open(str(backup_file), "w:gz") as tar:
                    tar.add(pth.stem)  # add all files in that directory
                print(
                    "A backup of {} has been made. It is at {}".format(
                        selected, backup_file
                    )
                )
                

async def remove_instance(selected, instance_data):
    instance_list = load_existing_config()
    if instance_data["STORAGE_TYPE"] == "MongoDB":
        m = Mongo("Core", **instance_data["STORAGE_DETAILS"])
        db = m.db
        collections = await db.collection_names(include_system_collections=False)
        for name in collections:
            collection = await db.get_collection(name)
            await collection.drop()
    else:
        pth = Path(instance_data["DATA_PATH"])
        safe_delete(pth)
    save_config(selected, {}, remove=True)
    print("The instance {} has been removed\n".format(selected))


async def remove_instance_interaction():
    instance_list = load_existing_config()
    if not instance_list:
        print("No instances have been set up!")
        return

    print(
        "You have chosen to remove an instance. The following "
        "is a list of instances that currently exist:\n"
    )
    for instance in instance_list.keys():
        print("{}\n".format(instance))
    print("Please select one of the above by entering its name")
    selected = input("> ")
    
    if selected not in instance_list.keys():
        print("That isn't a valid instance!")
        return
    instance_data = instance_list[selected]
   
    await create_backup(selected, instance_data)
    await remove_instance(selected, instance_data)


def main():
    if args.delete:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(remove_instance_interaction())
    elif args.edit:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(edit_instance())
    else:
        basic_setup()

args, _ = parse_cli_args()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting...")
    else:
        print("Exiting...")
