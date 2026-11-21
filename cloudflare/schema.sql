/* Paste into Cloudflare dashboard → D1 → your database → Console, then Execute.
   Only block comments here: the console joins lines, which would turn "--" comments into
   one long comment.
   watches: the watch list (body = settings JSON; meta = cheapest price, seller list from price runs)
   menus:   where each person is in the menu
   jobs:    work that needs Akakçe (new watch setup, prices now, summary), done by a GitHub run */
CREATE TABLE IF NOT EXISTS watches (id TEXT PRIMARY KEY, owner TEXT NOT NULL, pos INTEGER NOT NULL, body TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS menus (owner TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, created INTEGER NOT NULL);
