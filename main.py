import argparse
import difflib
import filecmp
import json
import os.path
import sys
import tempfile
from pathlib import Path
from binaryornot.check import is_binary
import zipfile

from requests import get
import urllib.parse
from retrying import retry


def get_args():
    parser = argparse.ArgumentParser(description='Octopus release diff.')
    parser.add_argument('--octopusUrl',
                        dest='octopus_url',
                        action='store',
                        help='The Octopus server URL',
                        required=True)
    parser.add_argument('--octopusApiKey',
                        dest='octopus_api_key',
                        action='store',
                        help='The Octopus API key',
                        required=True)
    parser.add_argument('--octopusSpace',
                        dest='octopus_space',
                        action='store',
                        help='The Octopus space',
                        required=True)
    parser.add_argument('--octopusProject',
                        dest='octopus_project',
                        action='store',
                        help='The project whose releases are compared',
                        required=True)
    parser.add_argument('--oldRelease',
                        dest='old_release',
                        action='store',
                        help='The previous release to compare',
                        required=False)
    parser.add_argument('--newRelease',
                        dest='new_release',
                        action='store',
                        help='The new release to compare',
                        required=False)
    return parser.parse_args()


def get_octopus_headers(args):
    return {"X-Octopus-ApiKey": args.octopus_api_key}


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def space_name_to_id(args):
    url = args.octopus_url + "/api/Spaces?partialName=" + urllib.parse.quote(args.octopus_space.strip()) + "&take=1000"
    response = get(url, headers=get_octopus_headers(args))
    spaces_json = response.json()

    filtered_items = [a for a in spaces_json["Items"] if a["Name"] == args.octopus_space.strip()]

    if len(filtered_items) == 0:
        sys.stderr.write("The space called " + args.octopus_space.strip() + " could not be found.\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def project_name_to_id(args, space_id):
    if space_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Projects?take=1000&partialName=" + urllib.parse.quote(
        args.octopus_project.strip())
    response = get(url, headers=get_octopus_headers(args))
    response_json = response.json()

    filtered_items = [a for a in response_json["Items"] if a["Name"] == args.octopus_project.strip()]

    if len(filtered_items) == 0:
        sys.stderr.write("The project called " + args.octopus_project.strip() + " could not be found.\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_release(args, space_id, project_id):
    if space_id is None or project_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Projects/" + project_id + "/Releases"
    response = get(url, headers=get_octopus_headers(args))
    response_json = response.json()

    # We need at least two releases to do a comparison
    if len(response_json["Items"]) < 2:
        return None

    # Use the two latest releases if none were defined
    if args.old_release is None or args.new_release is None:
        return {
            "source": response_json["Items"][1],
            "destination": response_json["Items"][0]
        }

    source = [a for a in response_json["Items"] if a["Version"] == args.old_release]
    dest = [a for a in response_json["Items"] if a["Version"] == args.new_release]

    if len(source) == 1 and len(dest) == 1:
        return {
            "source": source,
            "destination": dest
        }

    if len(source) != 1:
        print("Could not find old release " + args.old_release)

    if len(dest) != 1:
        print("Could not find new release " + args.new_release)

    return None


def get_deployment_process(args, space_id, deployment_process_id):
    if space_id is None or deployment_process_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/DeploymentProcesses/" + deployment_process_id
    response = get(url, headers=get_octopus_headers(args))
    return response.json()


def get_variables(args, space_id, variables_is):
    if space_id is None or variables_is is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Variables/" + variables_is
    response = get(url, headers=get_octopus_headers(args))
    return response.json()


def get_built_in_feed_id(args, space_id):
    if space_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Feeds?take=1000"
    response = get(url, headers=get_octopus_headers(args))
    built_in_feed = [a for a in response.json()["Items"] if a["FeedType"] == "BuiltIn"]

    if len(built_in_feed) == 1:
        return built_in_feed[0]["Id"]

    return None


def package_from_built_in_feed(built_in_feed_id, deployment_process, step_name, action_name, package_name):
    for step in deployment_process["Steps"]:
        if step["Name"] == step_name:
            for action in step["Actions"]:
                if action["Name"] == action_name:
                    for package in action["Packages"]:
                        if package["Name"] == package_name:
                            return package["FeedId"] == built_in_feed_id
    return False


def flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id, releases, get_deployment_process,
                                                 get_variables):
    """
    When performing a diff between two release we are interested in:
    * The packages included in the release
    * Whether the packages are sourced from the built-in feed (as we can download these packages)
    * The deployment process snapshotted with the release
    * The variables snapshotted with the release
    This function flattens the release, deployment process, variables, and additional information such as
    the source of the packages into a simple map. This map is what the rest of the script works with.
    """
    if releases is None or built_in_feed_id is None or space_id is None:
        return None

    releases_map = {}
    for release in releases.keys():
        deployment_process = get_deployment_process(args, space_id,
                                                    releases[release].get("ProjectDeploymentProcessSnapshotId"))
        variables = get_variables(args, space_id,
                                  releases[release].get("ProjectVariableSetSnapshotId"))
        packages = releases[release].get("SelectedPackages")
        if packages is not None:
            releases_map[release] = {
                "packages": [{
                    "id": a.get("PackageReferenceName"),
                    "version": a.get("Version"),
                    "from_built_in_feed": package_from_built_in_feed(built_in_feed_id,
                                                                     deployment_process,
                                                                     a.get("StepName"),
                                                                     a.get("ActionName"),
                                                                     a.get("PackageReferenceName")),
                } for a in packages],
                "deployment_process": deployment_process,
                "variables": variables.get("Variables"),
                "version": releases[release].get("Version")
            }

    return releases_map


def list_package_diff(release_packages, print_new_package, print_removed_package):
    for package in release_packages["destination"]["packages"]:
        if len([a for a in release_packages["source"]["packages"] if a["id"] == package["id"]]) == 0:
            print_new_package(package)

    for package in release_packages["source"]["packages"]:
        if len([a for a in release_packages["destination"]["packages"] if a["id"] == package["id"]]) == 0:
            print_removed_package(package)


def download_packages(args, space_id, release_packages, path):
    for package in release_packages["destination"]["packages"]:
        package["downloaded"] = download_package(args, space_id, package["id"], package["version"], path)

    for package in release_packages["source"]["packages"]:
        matching = [a for a in release_packages["destination"]["packages"] if
                    a["id"] == package["id"] and a["version"] == package["version"]]
        if len(matching) == 0:
            package["downloaded"] = download_package(args, space_id, package["id"], package["version"], path)
        else:
            package["downloaded"] = matching[0]["downloaded"]

    return release_packages


def download_package(args, space_id, package_id, package_version, path):
    if space_id is None or package_id is None or package_version is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Packages/packages-" + package_id + "." + package_version
    response = get(url, headers=get_octopus_headers(args))
    package = response.json()

    url = args.octopus_url + "/api/" + space_id + "/Packages/packages-" + package_id + "." + package_version + "/raw"
    response = get(url, headers=get_octopus_headers(args))
    file_path = os.path.join(path, package_id + "." + package_version + package['FileExtension'])
    with open(file_path, "wb") as f:
        f.write(response.content)

    return file_path


def extract_package(dir, archive):
    try:
        with zipfile.ZipFile(archive, 'r') as zip_ref:
            extract_dir = os.path.join(dir, Path(archive).stem)
            zip_ref.extractall(extract_dir)
            return extract_dir
    except:
        print("Failed to extract " + archive)

    return None


def extract_packages(release_packages_with_download):
    if release_packages_with_download is None:
        return None

    processed = {}
    for release_packages in release_packages_with_download.values():
        for package in release_packages["packages"]:
            if package["downloaded"] not in processed.keys():
                package["extracted"] = extract_package(temp_dir, package["downloaded"])
                processed[package["downloaded"]] = package["extracted"]
            else:
                package["extracted"] = processed[package["downloaded"]]

    return release_packages_with_download


def compare_directories(release_packages_with_extract, left_only, right_only, diff):
    if release_packages_with_extract is None:
        return None

    for dest_package in release_packages_with_extract["destination"]["packages"]:
        for source_package in release_packages_with_extract["source"]["packages"]:
            if dest_package["id"] == source_package["id"] and dest_package["version"] != source_package["version"]:
                report = filecmp.dircmp(dest_package["extracted"], source_package["extracted"])
                left_only(report.left_only, dest_package, source_package)
                right_only(report.right_only, dest_package, source_package)
                diff(report.diff_files, dest_package, source_package)


def print_added_files(releases, files, dest_package, source_package):
    if releases is None:
        return None

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " added the following files from "
              + dest_package["id"] + "." + dest_package["version"]
              + " compared to release " + releases["source"]["Version"] + " with package "
              + source_package["id"] + "." + source_package["version"] + ":\n\t"
              + "\n\t".join(files))


def print_removed_files(releases, files, dest_package, source_package):
    if releases is None:
        return None

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " removed the following files from "
              + dest_package["id"] + "." + dest_package["version"]
              + " compared to release " + releases["source"]["Version"] + " with package "
              + source_package["id"] + "." + source_package["version"] + ":\n\t"
              + "\n\t".join(files))


def print_changed_files(releases, files, dest_package, source_package):
    if releases is None:
        return None

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " changed the following files from "
              + dest_package["id"] + "." + dest_package["version"]
              + " compared to release " + releases["source"]["Version"] + " with package "
              + source_package["id"] + "." + source_package["version"] + ":\n\t"
              + "\n\t".join(files))

        print("")
        for file in files:
            source_file = os.path.join(source_package["extracted"], file)
            dest_file = os.path.join(dest_package["extracted"], file)
            if not (is_binary(source_file) or is_binary(dest_file)):
                text1 = open(source_file).readlines()
                text2 = open(dest_file).readlines()
                print("Diff of " + file + ":")
                for line in difflib.unified_diff(text1, text2):
                    print(line)


def print_changed_step(release):
    if releases is None:
        return None

    source_json = json.dumps(release["source"]["deployment_process"]["Steps"], indent=2)
    dest_json = json.dumps(release["destination"]["deployment_process"]["Steps"], indent=2)

    if source_json != dest_json:
        diff = difflib.unified_diff(source_json.split('\n'), dest_json.split('\n'))
        print("Changed detected in the deployment process:")
        for line in diff:
            print(line)


def display_welcome_info(release_packages):
    if release_packages is None:
        return None

    print("Inventory of changes in release " + release_packages["destination"]["version"]
          + " compared to release " + release_packages["source"]["version"] + ".")
    print("====================================================================")


def get_variable_changes(release_packages, print_new_variable, print_removed_variable, print_changed_variable):
    if release_packages is None:
        return None

    for variable in release_packages["destination"]["variables"]:
        if len([a for a in release_packages["source"]["variables"] if a["Name"] == variable["Name"]]) == 0:
            print_new_variable(variable)

    for variable in release_packages["source"]["variables"]:
        if len([a for a in release_packages["destination"]["variables"] if a["Name"] == variable["Name"]]) == 0:
            print_removed_variable(variable)

    for variable in release_packages["destination"]["variables"]:
        if len([a for a in release_packages["source"]["variables"] if
                a["Name"] == variable["Name"] and not a["IsSensitive"] and not a["Value"] == variable["Value"]]) != 0:
            print_changed_variable(variable)


args = get_args()
space_id = space_name_to_id(args)
project_id = project_name_to_id(args, space_id)
releases = get_release(args, space_id, project_id)
built_in_feed_id = get_built_in_feed_id(args, space_id)
release_packages = flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id, releases,
                                                                get_deployment_process, get_variables)
display_welcome_info(release_packages)
list_package_diff(release_packages,
                  lambda p: print("Release " + release_packages["destination"]["version"]
                                  + " added the package: " + p["id"]),
                  lambda p: print("Release " + release_packages["destination"]["version"]
                                  + " removed the package: " + p["id"]))
temp_dir = tempfile.mkdtemp()
release_packages_with_download = download_packages(args, space_id, release_packages, temp_dir)
release_packages_with_extract = extract_packages(release_packages_with_download)
compare_directories(release_packages_with_extract,
                    lambda files, dest, source: print_added_files(releases, files, dest, source),
                    lambda files, dest, source: print_removed_files(releases, files, dest, source),
                    lambda files, dest, source: print_changed_files(releases, files, dest, source))
print_changed_step(release_packages)
get_variable_changes(release_packages_with_extract,
                     lambda p: print("Release " + release_packages["destination"]["version"]
                                     + " added the variable: " + p["Name"]),
                     lambda p: print("Release " + release_packages["destination"]["version"]
                                     + " removed the variable: " + p["Name"]),
                     lambda p: print("Release " + release_packages["destination"]["version"]
                                     + " changed the value of the variable \"" + p["Name"] + "\" to \"" + p[
                                         "Value"] + "\""))
