import argparse
import base64
import difflib
import filecmp
import json
import os.path
import sys
import tempfile
from pathlib import Path

import numpy as np
from binaryornot.check import is_binary
import zipfile

from requests import get
import urllib.parse
from retrying import retry


def get_args():
    """
    Defines the command line arguments used by the script
    :return: the parsed arguments
    """
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
    """
    Generate the HTTP headers required to access the Octopus API
    :param args: parsed arguments
    :return: The API key headers
    """
    if args is None:
        return None
    return {"X-Octopus-ApiKey": args.octopus_api_key}


def print_output_var(name, value):
    """
    Creates an Octopus output variable
    :param name: The variable name
    :param value: The variable value
    """
    print("##octopus[setVariable name='" + base64.b64encode(
        name.encode("ascii")).decode("ascii") + "' value='" + base64.b64encode(
        value.encode("ascii")).decode("ascii") + "']")


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def space_name_to_id(args):
    """
    Convert a space name to an ID
    :param args: The parsed arguments
    :return: The ID of the space
    """
    if args is None:
        return None

    url = args.octopus_url + "/api/Spaces?partialName=" + urllib.parse.quote(args.octopus_space.strip()) + "&take=1000"
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    spaces_json = response.json()

    filtered_items = [a for a in spaces_json["Items"] if a["Name"] == args.octopus_space.strip()]

    if len(filtered_items) == 0:
        sys.stderr.write("The space called " + args.octopus_space.strip() + " could not be found.\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def project_name_to_id(args, space_id):
    """
    Convert a project name to an ID
    :param args: The parsed arguments
    :param space_id: The space ID
    :return: The project ID
    """
    if args is None or space_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Projects?take=1000&partialName=" + urllib.parse.quote(
        args.octopus_project.strip())
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    response_json = response.json()

    filtered_items = [a for a in response_json["Items"] if a["Name"] == args.octopus_project.strip()]

    if len(filtered_items) == 0:
        sys.stderr.write("The project called " + args.octopus_project.strip() + " could not be found.\n")
        return None

    first_id = filtered_items[0]["Id"]
    return first_id


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_release(args, space_id, project_id):
    """
    Get the detail of the two releases that are going to be compared
    :param args: The parsed arguments
    :param space_id: The space ID
    :param project_id: The project ID
    :return: The two releases that are to be compared
    """
    if args is None or space_id is None or project_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Projects/" + project_id + "/Releases"
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
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
            "source": source[0],
            "destination": dest[0]
        }

    if len(source) != 1:
        print("Could not find old release " + args.old_release)

    if len(dest) != 1:
        print("Could not find new release " + args.new_release)

    return None


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_deployment_process(args, space_id, deployment_process_id):
    """
    Get the deployment process
    :param args: The parsed arguments
    :param space_id: The space ID
    :param deployment_process_id: The deployment ID
    :return: The deployment process
    """
    if args is None or space_id is None or deployment_process_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/DeploymentProcesses/" + deployment_process_id
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    return response.json()


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_variables(args, space_id, variables_is):
    """
    Gets the variables
    :param args: The parsed arguments
    :param space_id: The space ID
    :param variables_is: The variable set ID
    :return: The variable set
    """
    if args is None or space_id is None or variables_is is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Variables/" + variables_is
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    return response.json()


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_built_in_feed_id(args, space_id):
    """
    Gets the ID of the built-in feed
    :param args: The parsed arguments
    :param space_id:
    :return: The built-in feed ID
    """
    if args is None or space_id is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Feeds?take=1000"
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    built_in_feed = [a for a in response.json()["Items"] if a["FeedType"] == "BuiltIn"]

    if len(built_in_feed) == 1:
        return built_in_feed[0]["Id"]

    return None


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def package_from_built_in_feed(built_in_feed_id, deployment_process, step_name, action_name, package_name):
    """
    Determines if a package is from the built-in feed
    :param built_in_feed_id: The built-in feed ID
    :param deployment_process: The deployment process
    :param step_name: The name of the step with the package
    :param action_name: The name of the action with the package
    :param package_name: The name of the package
    :return: True if the package is from the built-in feed, and False otherwise
    """
    if built_in_feed_id is None or deployment_process is None or step_name is None or action_name is None or package_name is None:
        return None

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
    if args is None or releases is None or built_in_feed_id is None or space_id is None or releases is None or get_deployment_process is None or get_variables is None:
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
    """
    Calculate the added and removed packages
    :param release_packages: The details of the releases to compare
    :param print_new_package: A callback to call with the details of new packages
    :param print_removed_package: A callback to call with the details of removed packages
    """
    if release_packages is None:
        return

    if print_new_package is not None:
        added_packages = []
        for package in release_packages["destination"]["packages"]:
            if len([a for a in release_packages["source"]["packages"] if a["id"] == package["id"]]) == 0:
                added_packages.append(package)
        print_new_package(added_packages)

    if print_removed_package is not None:
        removed_packages = []
        for package in release_packages["source"]["packages"]:
            if len([a for a in release_packages["destination"]["packages"] if a["id"] == package["id"]]) == 0:
                removed_packages.append(package)
        print_removed_package(removed_packages)


def download_packages(args, space_id, release_packages, path):
    """
    Download any packages found in the built-in feed
    :param args: The parsed arguments
    :param space_id: The space ID
    :param release_packages: The details of the release
    :param path: The path to download the packages to
    :return: The details of the releases with a download path added for each package
    """
    if args is None or space_id is None or release_packages is None or path is None:
        return None

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


@retry(stop_max_attempt_number=3, wait_fixed=2000)
def download_package(args, space_id, package_id, package_version, path):
    """
    Download a package from the built-in feed
    :param args: The parsed arguments
    :param space_id: The space ID
    :param package_id: The package ID
    :param package_version: The package version
    :param path: The path to download the file to
    :return: The path to the downloaded file
    """
    if args is None or space_id is None or package_id is None or package_version is None or path is None:
        return None

    url = args.octopus_url + "/api/" + space_id + "/Packages/packages-" + package_id + "." + package_version
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    package = response.json()

    url = args.octopus_url + "/api/" + space_id + "/Packages/packages-" + package_id + "." + package_version + "/raw"
    response = get(url, headers=get_octopus_headers(args))
    response.raise_for_status()
    file_path = os.path.join(path, package_id + "." + package_version + package['FileExtension'])
    with open(file_path, "wb") as f:
        f.write(response.content)

    return file_path


def extract_package(dir, archive):
    """
    Extract a zip file
    :param dir: The directory to extract into
    :param archive: The archive to extract
    :return: The path of the extracted archive
    """
    try:
        with zipfile.ZipFile(archive, 'r') as zip_ref:
            extract_dir = os.path.join(dir, Path(archive).stem)
            zip_ref.extractall(extract_dir)
            return extract_dir
    except:
        print("Failed to extract " + archive)

    return None


def extract_packages(releases, temp_dir):
    """
    Extract the packages associated with the releases
    :param releases: The details of the release
    :return: The detail;s of the releases with the extracted path added for each package
    """
    if releases is None or releases.get("source") is None or releases.get("destination") is None:
        return None

    processed = {}
    for release_packages in releases.values():
        for package in release_packages["packages"]:
            if package["downloaded"] not in processed.keys():
                package["extracted"] = extract_package(temp_dir, package["downloaded"])
                processed[package["downloaded"]] = package["extracted"]
            else:
                package["extracted"] = processed[package["downloaded"]]

    return releases


def compare_directories(releases, left_only, right_only, diff):
    """
    Compare the files from the packages associated with two releases
    :param releases: The details of the releases
    :param left_only: A callback to call with files added in the new release
    :param right_only: A callback to call with files removed in the new release
    :param diff: A callback to call with files that changed between releases
    """
    if releases is None or left_only is None or right_only is None or diff is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return

    for dest_package in releases["destination"]["packages"]:
        for source_package in releases["source"]["packages"]:
            if dest_package["id"] == source_package["id"] and dest_package["version"] != source_package["version"]:
                report = filecmp.dircmp(dest_package["extracted"], source_package["extracted"])
                left_only(report.left_only, dest_package, source_package)
                right_only(report.right_only, dest_package, source_package)
                diff(report.diff_files, dest_package, source_package)


def print_added_packages(releases, packages):
    """
    Print the details of added packages
    :param releases: The details of the releases to be compared
    :param packages: The list of pacakges added
    """
    if releases is None or packages is None:
        return

    for p in packages:
        print("Release " + release_packages["destination"]["version"]
              + " added the package: " + p["id"])


def print_removed_packages(releases, packages):
    """
    Print the details of removed packages
    :param releases: The details of the releases to be compared
    :param packages: The list of pacakges added
    """
    if releases is None or packages is None:
        return

    for p in packages:
        print("Release " + release_packages["destination"]["version"]
              + " removed the package: " + p["id"])


def output_added_packages(packages):
    """
    Print the details of added packages
    :param releases: The details of the releases to be compared
    :param packages: The list of pacakges added
    """
    if releases is None or packages is None:
        return

    print_output_var("Packages.Added", ",".join(packages))


def output_removed_packages(packages):
    """
    Print the details of removed packages
    :param releases: The details of the releases to be compared
    :param packages: The list of pacakges added
    """
    if releases is None or packages is None:
        return

    print_output_var("Packages.Removed", ",".join(packages))


def print_added_files(releases, files, dest_package, source_package):
    """
    Print the details of added files
    :param releases: The details of the releases to be compared
    :param files: The list of files added
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " added the following files in "
              + dest_package["id"] + "." + dest_package["version"]
              + " compared to release " + releases["source"]["Version"] + " with package "
              + source_package["id"] + "." + source_package["version"] + ":\n\t"
              + "\n\t".join(files))


def output_added_files(releases, files, dest_package, source_package):
    """
    Captures the details of added files as output variables
    :param releases: The details of the releases to be compared
    :param files: The list of files added
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return

    print_output_var("Files[" + dest_package["id"] + "].Added", ",".join(files))


def print_removed_files(releases, files, dest_package, source_package):
    """
    Print the details of removed files
    :param releases: The details of the releases to be compared
    :param files: The list of files removed
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " removed the following files from "
              + dest_package["id"] + "." + dest_package["version"]
              + " compared to release " + releases["source"]["Version"] + " with package "
              + source_package["id"] + "." + source_package["version"] + ":\n\t"
              + "\n\t".join(files))


def output_removed_files(releases, files, dest_package, source_package):
    """
    Captures the details of removed files as output variables
    :param releases: The details of the releases to be compared
    :param files: The list of files removed
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return

    print_output_var("Files[" + dest_package["id"] + "].Removed", ",".join(files))


def print_changed_files(releases, files, dest_package, source_package):
    """
    Print the details of changes files
    :param releases: The details of the releases to be compared
    :param files: The list of files changed
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return None

    if len(files) != 0:
        print("Release " + releases["destination"]["Version"]
              + " changed the following files in package "
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


def output_changed_files(releases, files, dest_package, source_package):
    """
    Print the details of changes files
    :param releases: The details of the releases to be compared
    :param files: The list of files changed
    :param dest_package: The new package details
    :param source_package: The old package details
    """
    if releases is None or files is None or dest_package is None or source_package is None \
            or releases.get("source") is None or releases.get("destination") is None:
        return None

    print_output_var("Files[" + dest_package["id"] + "].Changed", ",".join(files))

    for file in files:
        source_file = os.path.join(source_package["extracted"], file)
        dest_file = os.path.join(dest_package["extracted"], file)
        if not (is_binary(source_file) or is_binary(dest_file)):
            text1 = open(source_file).readlines()
            text2 = open(dest_file).readlines()
            full_diff = ""
            for line in difflib.unified_diff(text1, text2):
                full_diff += line
            print_output_var("FileDiff[" + dest_package["id"] + "].Files[" + file + "].Diff", full_diff)


def output_added_variable(vars):
    """
    Print the details of added variables
    :param vars: The list of added variables
    """
    if vars is None:
        return

    print_output_var("Variables.Added", ",".join(map(lambda var: var["Name"], vars)))


def output_removed_variable(vars):
    """
    Print the details of removed variables
    :param vars: The list of removed variables
    """
    if vars is None:
        return

    print_output_var("Variables.Removed", ",".join(map(lambda var: var["Name"], vars)))


def output_changed_variable(vars):
    """
    Print the details of changes variables
    :param vars: The changed variables
    """
    if vars is None:
        return None

    print_output_var("Variables.Changed", ",".join(map(lambda var: var["Name"], vars)))

    output_vars_as_json(vars, "Changed")


def output_changed_scope_variable(vars):
    """
    Print the details of changes variables
    :param vars: The changed variables
    """
    if vars is None:
        return None

    print_output_var("Variables.ScopeChanged", ",".join(map(lambda var: var["Name"], vars)))

    output_vars_as_json(vars, "ScopeChanged")


def output_vars_as_json(vars, key):
    """
    Print the details of changes variables
    :param vars: The changed variables
    """
    if vars is None:
        return None

    var_names = set(map(lambda var: var["Name"], vars))
    for var_name in var_names:
        named_vars = [a for a in vars if a["Name"] == var_name]
        for index, var in enumerate(named_vars):
            print_output_var("Variables[" + var["Name"] + " " + index + "]." + key, json.dumps(var))


def print_changed_step(releases, step_changed):
    """
    Print the details of any changed steps
    :param releases: The details of the releases to compare
    """
    if releases is None or releases.get("source") is None or releases.get("destination") is None:
        return

    source_json = json.dumps(releases["source"]["deployment_process"]["Steps"], indent=2)
    dest_json = json.dumps(releases["destination"]["deployment_process"]["Steps"], indent=2)

    if source_json != dest_json:
        diff = difflib.unified_diff(source_json.split('\n'), dest_json.split('\n'))
        output = ""
        for line in diff:
            output += line + "\n"

        step_changed(output)


def display_welcome_banner(release_packages):
    """
    Display a welcome banner
    :param release_packages: The details of the releases to compare
    """
    if release_packages is None:
        return

    print("Inventory of changes in release " + release_packages["destination"]["version"]
          + " compared to release " + release_packages["source"]["version"] + ".")


def display_package_diff_banner():
    """
    Display a package change banner
    """
    print("")
    print("=======================================================================================")
    print("Added and removed packages and changes to package content")
    print("=======================================================================================")


def display_variable_diff_banner():
    """
    Display a variable change banner
    """
    print("")
    print("=======================================================================================")
    print("Added and removed variables, changes to variable values, and changes to variable scopes")
    print("=======================================================================================")


def display_step_diff_banner():
    """
    Display a step change banner
    """
    print("")
    print("=======================================================================================")
    print("Changes between the steps")
    print("=======================================================================================")


def get_variable_changes(release_packages, print_new_variable, print_removed_variable, print_changed_variable,
                         print_scope_changed):
    """
    Determine any changes to variables between the two releases.
    :param release_packages: The details of the releases to compare
    :param print_new_variable: A callback to call with new variables
    :param print_removed_variable: A callback to call with removed variables
    :param print_changed_variable: A callback to call with changed variables
    :param print_scope_changed: A callback to call with variables that had scope changes
    :return:
    """
    if release_packages is None:
        return

    if print_new_variable is not None:
        new_variables = []
        for variable in release_packages["destination"]["variables"]:
            if len([a for a in release_packages["source"]["variables"] if a["Name"] == variable["Name"]]) == 0:
                new_variables.append(variable)

        print_new_variable(new_variables)

    if print_removed_variable is not None:
        removed_variables = []
        for variable in release_packages["source"]["variables"]:
            if len([a for a in release_packages["destination"]["variables"] if a["Name"] == variable["Name"]]) == 0:
                removed_variables.append(variable)

        print_removed_variable(removed_variables)

    if print_changed_variable is not None:
        changed_variables = []
        for variable in release_packages["destination"]["variables"]:
            diff = [a for a in release_packages["source"]["variables"] if
                    a["Id"] == variable["Id"] and not a["IsSensitive"] and not a["Value"] == variable["Value"]]
            if len(diff) != 0:
                variable["OldValue"] = diff[0]["Value"]
                changed_variables.append(variable)

        print_changed_variable(changed_variables)

    if print_scope_changed is not None:
        scope_changed_variables = []
        for variable in release_packages["destination"]["variables"]:
            diff = [a for a in release_packages["source"]["variables"] if
                    a["Id"] == variable["Id"] and (
                            not np.array_equiv(a["Scope"].get("Environment") or [],
                                               variable["Scope"].get("Environment") or [])
                            or not np.array_equiv(a["Scope"].get("Machines") or [],
                                                  variable["Scope"].get("Machines") or [])
                            or not np.array_equiv(a["Scope"].get("Actions") or [],
                                                  variable["Scope"].get("Actions") or [])
                            or not np.array_equiv(a["Scope"].get("Roles") or [], variable["Scope"].get("Roles") or [])
                            or not np.array_equiv(a["Scope"].get("Channels") or [],
                                                  variable["Scope"].get("Channels") or [])
                            or not np.array_equiv(a["Scope"].get("TenantTags") or [],
                                                  variable["Scope"].get("TenantTags") or [])
                            or not np.array_equiv(a["Scope"].get("Processes") or [],
                                                  variable["Scope"].get("Processes") or [])
                    )]
            if len(diff) != 0:
                variable["OldScope"] = diff[0]
                scope_changed_variables.append(variable)

        print_scope_changed(scope_changed_variables)


if __name__ == '__main__':
    args = get_args()
    space_id = space_name_to_id(args)
    project_id = project_name_to_id(args, space_id)
    releases = get_release(args, space_id, project_id)
    built_in_feed_id = get_built_in_feed_id(args, space_id)
    release_packages = flatten_release_with_packages_and_deployment(args, built_in_feed_id, space_id, releases,
                                                                    get_deployment_process, get_variables)
    display_welcome_banner(release_packages)

    display_package_diff_banner()

    # Display the diff in the output
    list_package_diff(release_packages,
                      lambda p: print_added_packages(release_packages, p),
                      lambda p: print_removed_packages(release_packages, p))

    # Capture the diff as output vars
    list_package_diff(release_packages, output_added_packages, output_removed_packages)

    temp_dir = tempfile.mkdtemp()
    release_packages_with_download = download_packages(args, space_id, release_packages, temp_dir)
    release_packages_with_extract = extract_packages(release_packages_with_download, temp_dir)
    compare_directories(release_packages_with_extract,
                        lambda files, dest, source: print_added_files(releases, files, dest, source),
                        lambda files, dest, source: print_removed_files(releases, files, dest, source),
                        lambda files, dest, source: print_changed_files(releases, files, dest, source))

    compare_directories(release_packages_with_extract,
                        lambda files, dest, source: output_added_files(releases, files, dest, source),
                        lambda files, dest, source: output_removed_files(releases, files, dest, source),
                        lambda files, dest, source: output_changed_files(releases, files, dest, source))

    display_step_diff_banner()
    print_changed_step(release_packages, lambda output: print(output))

    display_variable_diff_banner()
    get_variable_changes(release_packages_with_extract,
                         lambda vars: print(
                             "\n".join(map(lambda var: "Release " + release_packages["destination"]["version"]
                                                       + " added the variable: " + var["Name"], vars))),
                         lambda vars: print(
                             "\n".join(map(lambda var: "Release " + release_packages["destination"]["version"]
                                                       + " removed the variable: " + var["Name"], vars))),
                         lambda vars: print("\n".join(
                             map(lambda var: "Release " + release_packages["destination"]["version"]
                                             + " changed the value of the variable \"" + var["Name"] + "\" from \"" +
                                             var["Value"] + "\" to \"" + var[
                                                 "OldValue"] + "\"", vars))),
                         lambda vars: print("\n".join(
                             map(lambda var: "Release " + release_packages["destination"]["version"]
                                             + " changed the scope of the variable \"" + var["Name"] + "\"", vars))))

    get_variable_changes(release_packages_with_extract,
                         output_added_variable,
                         output_removed_variable,
                         output_changed_variable,
                         output_changed_scope_variable)
