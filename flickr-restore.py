#!/usr/bin/env python
import json
import os
import sys
import logging
import time

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from exif import DateHelper, GeoHelper
from flickr import FlickrHelper
import flickr

def save_credentials(creds, auth_token_file):
    creds_dict = {
        'refresh_token': creds.refresh_token,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret
    }

    with open(auth_token_file, 'w', encoding='utf-8') as f:
        json.dump(creds_dict, f)


def get_authorized_session(client_secrets_file, auth_token_file):

    scopes = ['https://www.googleapis.com/auth/photoslibrary']

    creds = None
    try:
        creds = Credentials.from_authorized_user_file(auth_token_file, scopes)
    except Exception as err:
        logging.debug("Error opening auth token file: {}".format(err))

    if not creds:
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secrets_file, scopes=scopes)
        creds = flow.run_local_server(port=0)

    session = AuthorizedSession(creds)
    save_credentials(creds, auth_token_file)
    return session


class PhotoUploader:
    def __init__(self, flickr, session):
        self.posted_count = 0
        self.flickr = flickr
        self.session = session
        self.google_albums_by_title = None
        self.done_albums_file = None

    def upload_photos(self):
        id_files = self.flickr.flickr_id_files
        logging.info("Uploading %s photos", len(id_files))

        for id, file in id_files:
            logging.info(f"Uploading photo: {id}, {file}")
            self.update_exif(id, file)
            upload_token = self.upload_photo(id, file)
            if not upload_token:
                continue
            photo_json = self.flickr.get_photo_json(id)
            if photo_json:
                self.add_photo_to_albums(photo_json, upload_token)
                self.add_photo_to_tags(photo_json, upload_token)
            self.flickr.done_id(id, upload_token)

    def add_photo_to_albums(self, photo_json, upload_token):
        for album in photo_json["albums"]:
            title = album["title"]
            album_json = self.flickr.get_album_json(title)
            if album_json:
                self.add_photo_to_album(album_json, photo_json, upload_token)

    def add_photo_to_tags(self, photo_json, upload_token):
        for tag in photo_json["tags"]:
            title = "tag " + tag["tag"]
            album_json = {"title": title, "description": "", "cover_photo": ""}
            self.add_photo_to_album(album_json, photo_json, upload_token)

    def add_photo_to_album(self, album_json, photo_json, upload_token):
        album_title = album_json["title"]
        google_album = self.get_or_create_google_album(album_json)
        cover_photo_id = self.get_album_cover_photo_id(album_json)
        flickr_photo_id = photo_json["id"]
        create_request_body = {
            "albumId": google_album["id"],
            "newMediaItems": [
                {
                    "simpleMediaItem": {"uploadToken": upload_token}
                }
            ]}

        if flickr_photo_id == cover_photo_id:
            create_request_body["albumPosition"] = {"position": "FIRST_IN_ALBUM"}

        logging.debug(f"adding {flickr_photo_id} to {album_title}")
        logging.debug(f"    req: {create_request_body}")

        post = lambda: self.session.post("https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate", json=create_request_body)
        self.posted(post, f"add {flickr_photo_id} to album {album_title}")

    def update_exif(self, flickr_photo_id, flickr_photo_file):
        if not self.flickr.has_photo_json(flickr_photo_id):
            return

        logging.debug("Going to update exif for photo: '%s'" % flickr_photo_id)
        path = self.flickr.get_photo_fspath(flickr_photo_file)
        geo = self.flickr.get_photo_lat_lon(flickr_photo_id)
        if geo:
            gh = GeoHelper(geo, path)
            gh.update_geo_exif()
        date = self.flickr.get_date_taken(flickr_photo_id)
        name = self.flickr.get_name(flickr_photo_id)
        dh = DateHelper(name, date, path)
        dh.update_date()

    def get_album_cover_photo_id(self, flickr_album):
        cover_photo_id = flickr_album["cover_photo"].rpartition("/")
        if cover_photo_id[1] == "/":
            cover_photo_id = cover_photo_id[2]
        else:
            cover_photo_id = None

    def get_or_create_google_album(self, flickr_album):
        google_album = self.get_google_album(flickr_album["title"])
        if google_album:
            return google_album
        return self.create_google_album(flickr_album)

    def create_google_album(self, flickr_album):
        album_title = flickr_album["title"]

        logging.info(f"Creating google album: {album_title}")
        post = lambda: self.session.post("https://photoslibrary.googleapis.com/v1/albums", json={"album": {"title": album_title}})
        resp = self.posted(post,f"create google album: {album_title}")

        if not resp:
            return None

        google_album = resp.json()

        if "id" not in google_album:
            logging.info(f"No id in google_album: {google_album}")
            return None

        self.save_google_album(album_title, google_album)

        if flickr_album["description"]:
            self.set_album_description(google_album["id"], flickr_album["description"])

        return google_album

    def set_album_description(self, google_album_id, description):
        description = self.convert_description(description, "album")

        enrich_req_body = {
            "newEnrichmentItem": {
                "textEnrichment": {
                    "text": description
                }
            },
            "albumPosition": {
                "position": "FIRST_IN_ALBUM"
            }
        }
        r = self.session.post("https://photoslibrary.googleapis.com/v1/albums/%s:addEnrichment" % google_album_id, json=enrich_req_body)
        logging.debug("Enrich album response: {}".format(r.text))

    def convert_description(self, description, item):
        description = description.replace("&quot;", "\"")
        description = description.replace("&amp;", "&")

        # https://developers.google.com/photos/library/reference/rest/v1/mediaItems/batchCreate#NewMediaItem
        if len(description) > 909:
            logging.info(f"replaced Too much description text for {item}")
            description = "Too much description text"
        return description

    def upload_photo(self, flickr_photo_id, flickr_photo_file):
        path = self.flickr.get_photo_fspath(flickr_photo_file)
        with open(path, 'rb') as f:
            headers = {
                "X-Goog-Upload-File-Name": flickr_photo_file,
                "X-Goog-Upload-Protocol": "raw"
            }
            post = lambda: self.session.post("https://photoslibrary.googleapis.com/v1/uploads", data=f, headers=headers)
            resp = self.posted(post, f"upload {flickr_photo_id}, {flickr_photo_file}")

        if not resp:
            return None

        upload_token = resp.text
        logging.debug("Received upload token: %s" % upload_token)

        description = self.flickr.get_photo_description(flickr_photo_id)
        if description == "":
            return upload_token
        description = self.convert_description(description, flickr_photo_id)

        create_request_body = {
            "newMediaItems": [
                {
                    "description": description,
                    "simpleMediaItem": {"uploadToken": upload_token}
                }
            ]}

        post = lambda: self.session.post("https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate", json=create_request_body)
        self.posted(post, f"set description {flickr_photo_id}, {flickr_photo_file}")
        return upload_token

    def posted(self, post, action):
        for i in range(1, 5):
            resp = post()
            if resp.status_code == 200:
                self.posted_count += 1
                return resp

            # "error": {
            #   "code": 429,
            #   "message": "Quota exceeded for quota metric 'Write requests' and limit 'Write requests per minute per user' of service 'photoslibrary.googleapis.com' for consumer 'project_number:...'.",
            if resp.status_code != 429:
                logging.debug(f"    {resp.status_code}: {resp.text}")

            # https://developers.google.com/photos/library/guides/best-practices#retrying-failed-requests
            time.sleep(31)

        logging.info(f"    Failed: {action}")
        if resp.status_code == 429:
            logging.info("    Possible quota limit: https://developers.google.com/photos/overview/api-limits-quotas")
        logging.info(f"    Exit on posted count: {self.posted_count}")
        exit(1)
        return None

    def get_google_album(self, title):
        if self.google_albums_by_title is None:
            self.get_google_albums()
        return self.google_albums_by_title.get(title)

    def save_google_album(self, title, google_album):
        if self.google_albums_by_title is None:
            self.google_albums_by_title = {}
        self.google_albums_by_title[title] = google_album
        if self.done_albums_file:
            f = self.done_albums_file
            a = json.dumps([title, google_album])
            f.write(f"{a}\n")
            f.flush()

    def get_google_albums(self):
        if os.access("done_albums.txt", os.R_OK):
            with open("done_albums.txt", 'r') as f:
                for line in f:
                    title, album = json.loads(line)
                    self.save_google_album(title, album)

            logging.info(f"Read {len(self.google_albums_by_title)} albums from done_albums.txt")

            self.done_albums_file = open("done_albums.txt", 'a')

            return

        self.done_albums_file = open("done_albums.txt", 'a')
        self.google_albums_by_title = {}

        params = {'excludeNonAppCreatedData': True}

        while True:
            albums = self.session.get('https://photoslibrary.googleapis.com/v1/albums', params=params).json()
            logging.debug("Retrieved album list: %s" % albums)
            for album in albums.get("albums", []):
                title = album["title"]
                logging.debug("Found existing album: '{}'".format(title))
                self.save_google_album(title, album)

            if 'nextPageToken' in albums:
                params["pageToken"] = albums["nextPageToken"]
            else:
                break

def main(config):
    flickr = FlickrHelper(
        config["flickr_photo_dir"],
        config["flickr_photo_json_dir"],
        config["flickr_albums_json"]
    )

    session = get_authorized_session(
        config["client_secrets_file"],
        config["auth_token_file"]
    )

    uploader = PhotoUploader(flickr, session)
    uploader.upload_photos()

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s %(levelname)-8s %(message)s',
                        datefmt='%m-%d %H:%M',
                        filename='flickr-restore.log',
                        filemode='w')

    # define a Handler which writes INFO messages or higher to the sys.stderr
    formatter = logging.Formatter('%(levelname)-8s %(message)s')
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)

    if len(sys.argv) != 2:
        logging.error("Missing argument: config.json")
        exit(1)

    with open(sys.argv[1], "r") as f:
        config = json.load(f)
        main(config)
