import os
import math
import argparse

def convert_to_bytes(key_size_str):
    """Convert a key size string (e.g., '1KB', '100MB') to bytes."""
    size_units = {
        'KB': 1024,
        'MB': 1024**2,
        'GB': 1024**3,
        'TB': 1024**4,
    }

    # Split the number from the unit.
    for unit in size_units:
        if key_size_str.endswith(unit):
            number = float(key_size_str[:-len(unit)])
            return int(number * size_units[unit])

    raise ValueError(f"Invalid key size format: '{key_size_str}'")

def generate_workload(workload_name, key_size_str, operations_cnt, record_cnt):
    ozonedb_home = os.environ.get("OZONEDB_HOME")
    if not ozonedb_home:
        raise EnvironmentError("OZONEDB_HOME environment variable is not set.")

    ycsb_path = os.path.join(ozonedb_home, "ycsb")
    key_size = convert_to_bytes(key_size_str)
    workload_name = "workload"+workload_name
    input_file = ycsb_path + f"/workloads/{workload_name}"
    output_dir = ycsb_path + "/workloads/generated_workloads"
    output_file = os.path.join(output_dir, f"{workload_name}_{key_size_str}_{operations_cnt}_{record_cnt}")

    # The base workload file must exist.
    if not os.path.isfile(input_file):
        print(f"Error: The file '{input_file}' does not exist.")
        return

    os.makedirs(output_dir, exist_ok=True)
    if os.path.isfile(output_file):
        print(f"Workload File exist!")
        return

    with open(input_file, 'r') as file:
        lines = file.readlines()

    field_length = math.ceil(key_size / 10)
    modified_lines = []
    
    for line in lines:
        if line.startswith("recordcount="):
            modified_lines.append(f"recordcount={record_cnt}\n")
        elif line.startswith("operationcount="):
            modified_lines.append(f"operationcount={operations_cnt}\n")
        else:
            modified_lines.append(line)
            
    with open(output_file, 'w') as file:
        file.writelines(modified_lines)
    print(f"Workload generated: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate modified workload files.")
    parser.add_argument("--workload_name", type=str, help="Name of the workload (without extension)")
    parser.add_argument("--key_size", type=str, help="Size of the key (e.g., '1KB', '100MB')")
    parser.add_argument("--operation_cnt", type=int, help="Number of operations")
    parser.add_argument("--record_cnt", type=int, help="Number of records")
    

    args = parser.parse_args()

    generate_workload(args.workload_name, args.key_size, args.operation_cnt, args.record_cnt)
