import asyncio
import datetime
import os
import subprocess
import sys
import traceback

import evdev

from key_mapping import decode_key

# TODO:
# - if the path is just a directory, queue each file in the directory (up to X)
# - this means changing the queue so it contains filenames not identifiers
# - fade out volume before cutting shairport?
# - remember shairport and my volumes to leave them where they are
# - volume keys?


ROOT = os.path.abspath("/home/pi/music")
COMMAND_TIMEOUT = 3             # if user doesn't queue, forget numbers after 3s
YIELD_AUDIO_AFTER = 20
INDEX_FILENAME = "index.txt"
DEVICE = "/dev/input/event0"    # use desired keyboard
ALSA_CHANNEL_NAME = "Speaker"   # for controlling volume
VOL_STEP = 5
OUTPUT_DEVICE = "hw:S2"        # for mpg123


def get_time(event):
    """Return the time as floating point seconds since epoch."""
    return event.sec + 0.000001 * event.usec


master, slave = os.openpty()


class Jukebox():
    """A simple mp3 jukebox that queues up songs based on number entry."""

    def __init__(self):
        """Set up command listener and player loops."""
        self._entered_numbers = []
        self._play_process = None
        self._queue = []
        self._audio_open = False
        self._index = None
        self._last_event_time = 0.0

        self._player = asyncio.create_task(self._player_loop())
        self._key_worker = asyncio.create_task(self._key_loop())
        self._load_index()

    def _load_index(self):
        """Load the index file."""
        self._index = {}
        with open(os.path.join(ROOT, INDEX_FILENAME), "r") as file:
            for i, line in enumerate(file.readlines()):
                line = line.rstrip("\n")
                line = line.rstrip("\r")
                if not line:
                    continue
                if line.startswith("#"):
                    continue
                try:
                    identifer, path = line.split(" ", 1)
                    self._index[identifer] = path
                except ValueError:
                    print("Could not read line {} from index ('{}')".format(i, line))
        print("Index loaded")

    async def _key_loop(self):
        """Wait for new keyboard events and pass to handler."""
        while 1:
            try:
                device = evdev.InputDevice(DEVICE)
                async for event in device.async_read_loop():
                    self._handle_key_event(event)
            except OSError:
                print("Warning: Cannot read device. Retrying.")
                await asyncio.sleep(1)
            except BaseException as e:
                print("Fatal. Unhandled exception in key loop")
                traceback.print_exc(file=sys.stdout)
                break

    def _handle_key_event(self, event):
        """Handle key event."""
        if event.type != evdev.ecodes.EV_KEY:
            return

        if event.value != 0:   # only interested in "KEY UP" events
            return

        keycode = decode_key(event)
        if keycode is None:
            return

        event_time = get_time(event)
        if event_time == self._last_event_time:
            # ignore repeated events with same timestamp. This handles the
            # fact that the = key sends a burst on the same timestamp
            return

        time_elapsed = event_time - self._last_event_time
        if time_elapsed > COMMAND_TIMEOUT:
            self._entered_numbers = []

        self._last_event_time = event_time

        self._handle_keycode(keycode)

    def _handle_keycode(self, keycode):
        """Do required action given by keycode."""
        if keycode in '0123456789':
            self._entered_numbers.append(keycode)
            return

        identifier = "".join(self._entered_numbers)
        self._entered_numbers = []

        if keycode in ['QUEUE', 'NEXT']:
            if keycode == "QUEUE":
                self._queue.append(identifier)
            else:
                self._queue.insert(0, identifier)

            print(
                "Accepted new identifer ({}). Queue is now {}.".format(
                identifier, self._queue)
            )

        elif keycode == 'SKIP':
            self._skip()

        elif keycode in ['VOL+', 'VOL-']:
            
            plus_or_minus = keycode[-1]
            cmds = [
                "amixer", "set", ALSA_CHANNEL_NAME,
                "{}%{}".format(VOL_STEP, plus_or_minus)
            ]
            print(f'Changing volume with {" ".join(cmds)}')
            subprocess.run(cmds, capture_output=True)

        elif keycode == 'PAUSE' and self._play_process is not None:
            os.write(slave, b's')

        elif keycode == 'REWIND' and self._play_process is not None:
            os.write(slave, b','*50)

        else:
            print("Unhandled keycode '{}'".format(keycode))

    async def _player_loop(self):
        """Keep checking queue and play next song."""
        idle_seconds = 0
        while 1:
            try:
                if not self._queue:
                    print("Queue empty. Waiting for input.")
                while not self._queue:
                    await asyncio.sleep(1)
                    idle_seconds += 1
                    if idle_seconds > YIELD_AUDIO_AFTER:
                        self._close_audio()
                        idle_seconds = 1
                idle_seconds = 0
                identifier = self._queue.pop(0)
                await self._play(identifier)
            except FileNotFoundError:
                print("File not found")
                asyncio.sleep(1)

            except BaseException as e:
                print("Fatal. Unhandled exception in player loop")
                traceback.print_exc(file=sys.stdout)
                break

    def _skip(self):
        """Skip playing the current song."""
        if self._play_process is not None:
            print("Stopping play process")
            self._play_process.terminate()

    async def _play(self, identifier):
        """Play the song."""
        try:
            path = self._index[identifier]
        except KeyError:
            print("Can't find song {}".format(identifier))
            self._load_index()
            try:
                path = self._index[identifier]
            except KeyError:
                print("Still can't find song. Giving up.".format(identifier))
                return

        if path.endswith(".mp3"):
            targets = [os.path.join(ROOT, path)]
        else:
            # Not a file. Assume directory and play all files in it
            files = os.listdir(os.path.join(ROOT, path))
            targets = sorted([
                os.path.join(ROOT, path, fn) for fn in files
                if fn.endswith(".mp3")
            ])

        self._open_audio()
        print("Playing {} ({})".format(identifier, targets))
        self._play_process = await asyncio.create_subprocess_exec(
            "/usr/bin/mpg123", '-q', '-C', '-a', OUTPUT_DEVICE, '--rva-mix', *targets,
            stdin=master
        )
        await self._play_process.wait()
        self._play_process = None
        print(
            "Finished playing {}. Queue is now {}.".format(
                identifier, self._queue)
        )

    def _open_audio(self):
        """Stop the shairport process so we can use audio."""
        if not self._audio_open:
            print("Stopping shairport")
            subprocess.run(['service', 'shairport-sync', 'stop'])
            self._audio_open = True

    def _close_audio(self):
        """Start the shairport process as we're done playing for a while."""
        if self._audio_open:
            print("Starting shairport")
            subprocess.run(['service', 'shairport-sync', 'start'])
            self._audio_open = False

    def _close(self):
        """Close down all coroutines."""
        self._skip()
        self._key_worker.cancel()
        self._player.cancel()


async def main():
    jukebox = Jukebox()
    while 1:
        await asyncio.sleep(10)


asyncio.run(main())
