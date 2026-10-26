"""Generate a YCSB workload file for the crsqlite benchmark.

The script copies a base workload file and sets the record count and the operation
count. See generate_workload() for the details.
"""
import os
import argparse


def generate_workload(workload_name, key_size_str, operations_cnt, record_cnt):
    """Copy ycsb/workloads/workload{X} into workloads/generated_workloads/ and
    rewrite only the recordcount= and operationcount= lines. The key-size
    string appears only in the filename. Like the ozonedb generator, the record
    size stays at YCSB's default fieldcount=10 x fieldlength=100 = about 1000 B.
    That is what "1KB" denotes.

    Cached: the function returns early if the output file already exists.
    """
    crsqlite_home = os.environ.get("CRSQLITE_HOME")
    if not crsqlite_home:
        raise EnvironmentError("CRSQLITE_HOME environment variable is not set.")

    ycsb_path = os.path.join(crsqlite_home, "ycsb")
    workload_name = "workload" + workload_name
    input_file = os.path.join(ycsb_path, "workloads", workload_name)
    output_dir = os.path.join(ycsb_path, "workloads", "generated_workloads")
    output_file = os.path.join(
        output_dir, f"{workload_name}_{key_size_str}_{operations_cnt}_{record_cnt}"
    )

    if not os.path.isfile(input_file):
        print(f"Error: The file '{input_file}' does not exist.")
        return

    os.makedirs(output_dir, exist_ok=True)
    if os.path.isfile(output_file):
        print("Workload File exist!")
        return

    with open(input_file, "r") as file:
        lines = file.readlines()

    modified_lines = []
    for line in lines:
        if line.startswith("recordcount="):
            modified_lines.append(f"recordcount={record_cnt}\n")
        elif line.startswith("operationcount="):
            modified_lines.append(f"operationcount={operations_cnt}\n")
        else:
            modified_lines.append(line)

    with open(output_file, "w") as file:
        file.writelines(modified_lines)
    print(f"Workload generated: {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate modified workload files.")
    parser.add_argument("--workload_name", type=str, help="Name of the workload (a, b, c, d, f)")
    parser.add_argument("--key_size", type=str, help="Record-size label used in filenames (e.g. '1KB')")
    parser.add_argument("--operation_cnt", type=int, help="Number of operations")
    parser.add_argument("--record_cnt", type=int, help="Number of records")

    args = parser.parse_args()
    generate_workload(args.workload_name, args.key_size, args.operation_cnt, args.record_cnt)
