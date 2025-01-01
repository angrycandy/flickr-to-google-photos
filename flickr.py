import json
import os
import re
import logging

class FlickrHelper:
    def __init__(self, flickr_photo_dir, flickr_photo_json_dir, flickr_albums_json):
        self.flickr_photo_dir = flickr_photo_dir
        self.flickr_photo_json_dir = flickr_photo_json_dir
        self.flickr_albums_json = flickr_albums_json
        self.flickr_id_files = self.get_id_files()
        self.flickr_done_file = open("done_ids.txt", 'a')
        self.flickr_album_titles = self.get_album_titles()

    def get_id_files(self):
        if os.access("id_files.json", os.R_OK):
            logging.info("Read %s", "id_files.json")
            with open("id_files.json", 'r') as f:
                return self.remove_done_ids(json.load(f))

        id_files = []

        for dirpath, _, filenames in os.walk(self.flickr_photo_dir):
            for name in filenames:
                id = get_last_integer(name)
                id_files.append([id, name])

        with open("id_files.json", 'w') as f:
            json.dump(id_files, f, indent=2)
            logging.info(f"Wrote {len(id_files)} ids to id_files.json")

        return self.remove_done_ids(id_files)

    def remove_done_ids(self, id_files):
        if not os.access("done_ids.txt", os.R_OK):
            return id_files

        done_ids = set()
        with open("done_ids.txt", 'r') as f:
            for line in f:
                done_ids.add(re.split(r'\s+', line)[0])

        logging.info(f"Skipping {len(done_ids)} of {len(id_files)} already done as listed in done_ids.txt")

        undone = []
        for id_file in id_files:
            id = id_file[0]
            if id not in done_ids:
                undone.append(id_file)
        return undone

    def done_id(self, id, upload_token):
        f = self.flickr_done_file
        f.write(f"{id} {upload_token}\n")
        f.flush()

    def get_done_id_file(self):
        logging.info("Read %s", "done_ids.txt")
        return open("done_ids.txt", 'wr+')

    def get_album_json(self, title):
        return self.flickr_album_titles.get(title)

    def get_album_titles(self):
        titles = {}
        albums = self.get_all_albums()
        for album in albums:
            titles[album["title"]] = album

        return titles

    def get_all_albums(self):
        with open(self.flickr_albums_json, "r") as json_file:
            flickr_albums = json.load(json_file)
            return flickr_albums["albums"]

    def get_photo_fspath(self, photo_file):
        return os.path.join(self.flickr_photo_dir, photo_file)

    def get_photo_description(self, photo_id):
        photo_json = self.get_photo_json(photo_id)
        if photo_json:
            return "\n\n".join(filter(len, (photo_json["name"], photo_json["description"])))
        else:
            return ""

    def get_photo_lat_lon(self, photo_id):
        photo_json = self.get_photo_json(photo_id)
        if not photo_json:
            return None
        geos = photo_json["geo"]
        try:
            geo = geos[0]
            return geo
        except IndexError:
            return None

    def get_date_taken(self, photo_id):
        photo_json = self.get_photo_json(photo_id)
        return photo_json["date_taken"]

    def get_name(self, photo_id):
        photo_json = self.get_photo_json(photo_id)
        return photo_json["name"]

    def has_photo_json(self, photo_id):
        photo_json_file = os.path.join(self.flickr_photo_json_dir, "photo_%s.json" % photo_id)
        return os.path.isfile(photo_json_file)

    def get_photo_json(self, photo_id):
        photo_json_file = os.path.join(self.flickr_photo_json_dir, "photo_%s.json" % photo_id)
        try:
            with open(photo_json_file, "r") as json_file:
                photo_json = json.load(json_file)
                return photo_json
        except Exception as err:
            logging.warn(f"Photo json file {photo_json_file} read error {err}")
            return None

def get_last_integer(filename):
    last = None
    name, _ext = os.path.splitext(filename)
    parts = name.split('_')
    for part in parts:
        if part.isdigit():
            last = part
    return last
