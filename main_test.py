import contextlib
import json
import os
import subprocess
import tempfile
import time
import unittest

import numpy as np
import requests
from testcontainers.compose import DockerCompose
import main


def wait_for_resource_available(url, timeout):
    timer = 0
    while True:
        if timer > timeout:
            break
        try:
            resp = requests.get(url)
            resp.raise_for_status()
            break
        except:
            print("Sleeping")
            time.sleep(10)
            timer += 10


def apply_terraform(vars):
    args = ["terraform", "apply", "-auto-approve", "-var=octopus_server=http://localhost:8080",
            "-var=octopus_apikey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345",
            "-var=octopus_space_id=Spaces-1"]
    p = subprocess.Popen(np.concatenate((args, vars)), cwd="test/terraform")
    p.communicate()


def start_octopus():
    wait_for_resource_available("http://localhost:8080/api", 60)

    with contextlib.suppress(FileNotFoundError):
        os.remove("test/terraform/terraform.tfstate")
        os.remove("test/terraform/terraform.tfstate.backup")
        os.remove("test/terraform/.terraform.lock.hcl")

    p = subprocess.Popen(["terraform", "init"], cwd="test/terraform")
    p.communicate()

    apply_terraform([])

    p = subprocess.Popen(
        ["octo",
         "push",
         "--package=test/packages/package.0.0.1.zip",
         "--package=test/packages/package.0.0.2.zip",
         "--package=test/packages/anotherpackage.0.0.1.zip",
         "--server=http://localhost:8080",
         "--apiKey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"])
    p.communicate()


def build_args():
    return type('obj', (object,), {
        "octopus_url": "http://localhost:8080",
        "octopus_api_key": "API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345",
        "octopus_space": "Default",
        "octopus_project": "ReleaseDiffTest",
        "old_release": None,
        "new_release": None
    })


def create_release(package_version):
    p = subprocess.Popen(
        ["octo", "create-release", "--project=ReleaseDiffTest", "--defaultPackageVersion=" + package_version,
         "--server=http://localhost:8080", "--apiKey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"])
    p.communicate()


def clear_steps():
    deployment_process = requests.get(
        "http://localhost:8080/api/Spaces-1/deploymentprocesses/deploymentprocess-Projects-1",
        headers={"X-Octopus-ApiKey": "API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"}).json()
    deployment_process["Steps"] = []
    requests.put("http://localhost:8080/api/Spaces-1/deploymentprocesses/deploymentprocess-Projects-1",
                 json.dumps(deployment_process),
                 headers={"X-Octopus-ApiKey": "API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"})


class LambdaTracker:
    def __init__(self):
        self.call_map = {}

    def track_call(self, call):
        self.call_map[call] = True

    def track_call_with_data(self, call, data):
        self.call_map[call] = data

    def was_called(self, call):
        return call in self.call_map.keys()

    def was_called_with_data(self, call, data):
        return call in self.call_map.keys() and self.call_map[call] == data

    def get_call(self, call):
        return self.call_map[call]


class TestSum(unittest.TestCase):
    def test_release_diff(self):
        call_tracker = LambdaTracker()
        with DockerCompose(os.getcwd(), compose_file_name=["compose.yaml"], pull=True) as compose:
            start_octopus()

            create_release("0.0.1")

            clear_steps()

            apply_terraform(["-var=echo_message=there", "-var=package_id=anotherpackage",
                             "-var=releasedifftest_variable1_2=newvalue"])

            create_release("0.0.1")

            args = build_args()

            space_id = main.space_name_to_id(args)
            project_id = main.project_name_to_id(args, space_id)
            releases = main.get_release(args, space_id, project_id)
            built_in_feed_id = main.get_built_in_feed_id(args, space_id)
            release_packages = main.flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id,
                                                                                 releases,
                                                                                 main.get_deployment_process,
                                                                                 main.get_variables)

            main.list_package_diff(release_packages,
                                   lambda p: call_tracker.track_call_with_data("package_added", p["id"]),
                                   lambda p: call_tracker.track_call_with_data("package_removed", p["id"]))
            self.assertTrue(call_tracker.was_called_with_data("package_added", "anotherpackage"))
            self.assertTrue(call_tracker.was_called_with_data("package_removed", "package"))

            temp_dir = tempfile.mkdtemp()
            release_packages_with_download = main.download_packages(args, space_id, release_packages, temp_dir)
            release_packages_with_extract = main.extract_packages(release_packages_with_download, temp_dir)
            main.compare_directories(release_packages_with_extract,
                                     lambda files, dest, source: self.assertTrue(len(files) == 0),
                                     lambda files, dest, source: self.assertTrue(len(files) == 0),
                                     lambda files, dest, source: self.assertTrue(len(files) == 0))

            main.print_changed_step(release_packages, lambda output: call_tracker.track_call("step_changed"))
            self.assertTrue(call_tracker.was_called("step_changed"))

            main.get_variable_changes(release_packages_with_extract,
                                      lambda new: self.fail("No variables must be added"),
                                      lambda new: self.fail("No variables must be removed"),
                                      lambda new, old: call_tracker.track_call_with_data("variable_changed",
                                                                                         {'new': new, 'old': old}),
                                      lambda new, old: self.fail("No variable scopes must be changed"))

            self.assertTrue(call_tracker.get_call("variable_changed")["new"]["Name"] == "Variable1",
                            "The variable called \"Variable1\" must have been changed.")
            self.assertTrue(call_tracker.get_call("variable_changed")["new"]["Value"] == "newvalue",
                            "The variable called \"Variable1\" must have the new value \"newvalue\".")
            self.assertTrue(call_tracker.get_call("variable_changed")["old"]["Name"] == "Variable1",
                            "The variable called \"Variable1\" must have been changed.")
            self.assertTrue(call_tracker.get_call("variable_changed")["old"]["Value"] == "value1",
                            "The variable called \"Variable1\" must have been changed from \"value1\".")

    def test_file_changes(self):
        call_tracker = LambdaTracker()
        with DockerCompose(os.getcwd(), compose_file_name=["compose.yaml"], pull=True) as compose:
            start_octopus()

            create_release("0.0.1")
            create_release("0.0.2")

            args = build_args()

            space_id = main.space_name_to_id(args)
            project_id = main.project_name_to_id(args, space_id)
            releases = main.get_release(args, space_id, project_id)
            built_in_feed_id = main.get_built_in_feed_id(args, space_id)
            release_packages = main.flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id,
                                                                                 releases,
                                                                                 main.get_deployment_process,
                                                                                 main.get_variables)

            main.list_package_diff(release_packages,
                                   lambda p: self.fail("No new packages added"),
                                   lambda p: self.fail("No new packages removed"))

            temp_dir = tempfile.mkdtemp()
            release_packages_with_download = main.download_packages(args, space_id, release_packages, temp_dir)
            release_packages_with_extract = main.extract_packages(release_packages_with_download, temp_dir)
            main.compare_directories(release_packages_with_extract,
                                     lambda files, dest, source: call_tracker.track_call_with_data("files_added",
                                                                                                   files),
                                     lambda files, dest, source: call_tracker.track_call_with_data("files_removed",
                                                                                                   files),
                                     lambda files, dest, source: call_tracker.track_call_with_data("files_changed",
                                                                                                   files))

            self.assertTrue("file.txt" in call_tracker.get_call("files_changed"))
            self.assertTrue("file2.txt" in call_tracker.get_call("files_added"))
            self.assertTrue("file3.txt" in call_tracker.get_call("files_removed"))


if __name__ == '__main__':
    unittest.main()
