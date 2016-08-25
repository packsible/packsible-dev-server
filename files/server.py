#!/usr/bin/env python
import os
import time
import errno
import shlex
import subprocess
import stat
import warnings
from multiprocessing import Process, Event
from flask import Flask, request, jsonify


DEFAULT_PRESETUP_DIR = '/packsible-dev-server/presetup'


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def run_pre_setup_commands(env):
    """Run any executables in the /packsible-dev-server/presetup directory

    If the presetup commands fail the script will still continue
    """
    presetup_dir = os.environ.get('PACKSIBLE_DEV_PRESETUP_DIR', DEFAULT_PRESETUP_DIR)
    if not os.path.exists(presetup_dir):
        return

    for file_name in os.listdir(presetup_dir):
        file_mode = os.stat(os.path.join(presetup_dir, file_name)).st_mode
        is_executable = file_mode & stat.S_IXUSR and file_mode & stat.S_IXGRP and file_mode & stat.S_IXOTH

        if is_executable:
            pre_setup_response = subprocess.call(
                './%s' % file_name,
                cwd=presetup_dir,
                env=env,
            )

            if pre_setup_response != 0:
                warnings.warn('Presetup command "%s" failed.')


def run_setup(env, setup_command, command, source_dir, app_dir):
    # Run pre setup
    run_pre_setup_commands(env)

    # Run setup command
    setup_response = subprocess.call(setup_command, shell=True, cwd=app_dir,
                                     env=env)

    if setup_response != 0:
        raise Exception('Setup command failed: %s' % setup_command)


def load_source_to_app_directory(env, source_dir, app_dir):
    # Create a tarball using the shell
    response = subprocess.call(
        'git ls-files -c -o --exclude-standard | tar -czf %s/build.tar.gz -T -' % app_dir,
        shell=True,
        cwd=source_dir,
        env=env
    )

    if response != 0:
        raise Exception('Could not package directory')

    response = subprocess.call(
        'tar xvf build.tar.gz',
        shell=True,
        cwd=app_dir,
        env=env
    )

    if response != 0:
        raise Exception('Could not unpack application changes')

    os.remove(os.path.join(app_dir, 'build.tar.gz'))



def watch(kill_event, env, skip_setup, setup_command, command, source_dir, app_dir,
          load_to_app_directory):
    # Ensure that the app directory exists
    mkdir_p(app_dir)

    if load_to_app_directory.lower() == 'true':
        load_source_to_app_directory(env, source_dir, app_dir)

    if not skip_setup and setup_command:
        run_setup(env, setup_command, command, source_dir, app_dir)

    print "Starting up command: %s" % command
    process = subprocess.Popen(shlex.split(command), cwd=app_dir, env=env)

    while True:
        kill_event.wait(10)
        if kill_event.is_set():
            break
    print "Received Kill Event"

    while process.poll() is None:
        process.terminate()
        process.kill()
        time.sleep(0.5)


class DevServer(object):
    def __init__(self):
        self._process = None
        self.env = os.environ.copy()

    def refresh(self, skip_setup=False):
        # Kill old process
        print "Restarting the processs"
        self._kill_event.set()
        self._process.join()
        self.start(skip_setup=skip_setup)

    def start(self, skip_setup=False):
        kill_event = Event()
        process = Process(target=watch, args=(
            kill_event,
            self.env,
            skip_setup,
            os.environ.get('PACKSIBLE_DEV_SETUP_COMMAND'),
            os.environ['PACKSIBLE_DEV_COMMAND'],
            os.environ.get('PACKSIBLE_DEV_SOURCE_DIR', os.path.abspath('.')),
            os.environ.get('PACKSIBLE_DEV_APP_DIR', os.path.abspath('/app')),
            os.environ.get('PACKSIBLE_DEV_LOAD_TO_APP_DIR', 'False'),
        ))
        process.start()
        self._process = process
        self._kill_event = kill_event


app = Flask(__name__)
dev_server = DevServer()


@app.route('/', methods=['POST'])
def refresh():
    refresh_req = {}
    try:
        refresh_req = request.json
    except:
        pass

    refresh_options = refresh_req.get('refresh_options', {})
    dev_server.refresh(**refresh_options)
    return jsonify(dict(status='ok'))


@app.route('/env')
def get_env():
    return jsonify(dev_server.env)


@app.route('/env/append', methods=['POST'])
def append_env():
    append_env_req = request.json

    append_env = append_env_req.get('env', {})
    for env_name, env_value in append_env.iteritems():
        dev_server.env[env_name] = env_value

    refresh_options = append_env_req.get('refresh_options', {})
    dev_server.refresh(**refresh_options)
    return jsonify(dev_server.env)


def main():
    # Start the build process
    dev_server.start()

    app.run(
        host=os.environ.get("PACKSIBLE_DEV_HOST", "0.0.0.0"),
        port=int(os.environ.get('PACKSIBLE_DEV_PORT', '31111'))
    )


if __name__ == "__main__":
    main()
