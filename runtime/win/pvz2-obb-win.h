/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * The game's OBB on Windows: the same data the Mac and iPad slim apps fetch on
 * their first start. It goes where the game looks first,
 * <data>/sdcard/Android/obb/com.ea.game.pvz2_rfl/, and it is checked (size and
 * sha256) before the game sees it.
 *
 * The values come from data/game-1.5.0.json, the build the port supports, and
 * they are the ones build.sh writes into the Mac app's Info.plist (the first
 * of obb_urls, obb_size, obb_sha256, obb_name). A new game version changes the
 * JSON and this header together.
 */
#ifndef PVZ2_OBB_WIN_H
#define PVZ2_OBB_WIN_H

#define PVZ2_OBB_NAME "main.675.com.ea.game.pvz2_rfl.obb"
#define PVZ2_OBB_SIZE 1435484160LL
#define PVZ2_OBB_SHA256 "8c667aa02852071983f63c96f8756cc41ad788c445f672fb562e4098eb53839e"
#define PVZ2_OBB_URL "https://rsb.ducnguyen.org/download/live/1794647945/full"

#endif
