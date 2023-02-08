import contextlib
import os
import subprocess
import tempfile
import time
import unittest
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


def start_octopus():
    wait_for_resource_available("http://localhost:8080/api", 60)

    with contextlib.suppress(FileNotFoundError):
        os.remove("test/terraform/terraform.tfstate")
        os.remove("test/terraform/terraform.tfstate.backup")
        os.remove("test/terraform/.terraform.lock.hcl")

    p = subprocess.Popen(["terraform", "init"], cwd="test/terraform")
    p.communicate()
    p = subprocess.Popen(["terraform", "apply", "-auto-approve", "-var=octopus_server=http://localhost:8080",
                          "-var=octopus_apikey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345",
                          "-var=octopus_space_id=Spaces-1"], cwd="test/terraform")
    p.communicate()

    p = subprocess.Popen(
        ["octo", "push", "--package=test/packages/package.0.0.1.zip", "--package=test/packages/package.0.0.2.zip",
         "--server=http://localhost:8080", "--apiKey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"])
    p.communicate()


class TestSum(unittest.TestCase):
    def test_no_releases(self):
        with DockerCompose(os.getcwd(),
                           compose_file_name=["compose.yaml"],
                           pull=True) as compose:
            start_octopus()

            p = subprocess.Popen(
                ["octo", "create-release", "--project=ReleaseDiffTest", "--defaultPackageVersion=0.0.1",
                 "--server=http://localhost:8080", "--apiKey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"])
            p.communicate()

            p = subprocess.Popen(
                ["octo", "create-release", "--project=ReleaseDiffTest", "--defaultPackageVersion=0.0.2",
                 "--server=http://localhost:8080", "--apiKey=API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"])
            p.communicate()

            args = type('obj', (object,), {
                "octopus_url": "http://localhost:8080",
                "octopus_api_key": "API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345",
                "octopus_space": "Default",
                "octopus_project": "ReleaseDiffTest",
                "old_release": "",
                "new_release": ""
            })

            space_id = main.space_name_to_id(args)
            project_id = main.project_name_to_id(args, space_id)
            releases = main.get_release(args, space_id, project_id)
            built_in_feed_id = main.get_built_in_feed_id(args, space_id)
            release_packages = main.flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id,
                                                                                 releases,
                                                                                 main.get_deployment_process,
                                                                                 main.get_variables)

            main.list_package_diff(release_packages,
                                   lambda p: print("Release " + release_packages["destination"]["version"]
                                                   + " added the package: " + p["id"]),
                                   lambda p: print("Release " + release_packages["destination"]["version"]
                                                   + " removed the package: " + p["id"]))
            temp_dir = tempfile.mkdtemp()
            release_packages_with_download = main.download_packages(args, space_id, release_packages, temp_dir)
            release_packages_with_extract = main.extract_packages(release_packages_with_download)
            main.compare_directories(release_packages_with_extract,
                                     lambda files, dest, source: self.fail("No files must be added"),
                                     lambda files, dest, source: self.fail("No files must be removed"),
                                     lambda files, dest, source: self.assertIn(self, "file.txt", files,
                                                                               "files must contain file.txt"))

            main.print_changed_step(release_packages, lambda output: self.fail("No steps must be changed"))

            main.get_variable_changes(release_packages_with_extract,
                                 lambda new: self.fail("No variables must be added"),
                                 lambda new: self.fail("No variables must be removed"),
                                 lambda new, old: self.fail("No variables must be changed"),
                                 lambda new, old: self.fail("No variable scopes must be changed"))


if __name__ == '__main__':
    unittest.main()
