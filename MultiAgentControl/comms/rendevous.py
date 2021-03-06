import setup_path 
import airsim

import numpy as np
import pprint
import time
import traceback
from scipy import floor, ceil
from utils.distance_utils import haversine


def enable_control(client, vehicle_names: list) -> None:
	for vehicle in vehicle_names:	
		client.enableApiControl(True, vehicle)
		client.armDisarm(True, vehicle)


def disable_control(client, vehicle_names: list) -> None:
	for vehicle in vehicle_names:                                                      
		client.armDisarm(False, vehicle)
		client.enableApiControl(False, vehicle)


def takeoff(client, vehicle_names: list) -> None:
	"""
	   Make all vehicles takeoff, one at a time and return the
	   pointer for the last vehicle takeoff to ensure we wait for
	   all drones  
	"""
	vehicle_pointers = []
	for vehicle in vehicle_names:
		vehicle_pointers.append(client.takeoffAsync(vehicle_name=vehicle))
	# All of this happens asynchronously. Hold the program until the last vehicle
	# finishes taking off.
	return vehicle_pointers[-1]


def get_all_drone_positions(client, vehicle_names: list, position_tracker: np.array) -> np.array:
	for i, vehicle in enumerate(vehicle_names):
		state_data = client.getMultirotorState(vehicle_name=vehicle)
		position_tracker[i] = [state_data.gps_location, state_data.kinematics_estimated.position] 
	return position_tracker


def update_communication_matrix(client, comm_matrix: np.array, positions: np.array, vehicle_names: list) -> bool:
	for i, parameter_list in enumerate(comm_matrix):
		for j, individual_list in enumerate(parameter_list):
			if i != j:
				# i will give you the current drone, e.g. "A"
				# j will give you the comparison drone, e.g. "B, C, ..."
				comparison_drone = positions[j][0]
				# Get comms data for the drone we want "A" in relation to the comparison drone "B, C, ..."
				comm_matrix[i,j] = client.getCommunicationsData(comparison_drone.latitude, comparison_drone.longitude, comparison_drone.altitude, vehicle_name=vehicle_names[i]).can_communicate
			else:
				comm_matrix[i,j] = True


def position_to_list(position_vector) -> list:
	return [position_vector.x_val, position_vector.y_val, position_vector.z_val]


def propagate_coordinates(client, comm_matrix: np.array, positions: np.array, vehicle_names: list):
	new_positions = np.zeros((len(vehicle_names)), dtype=list)
	for i, position in enumerate(new_positions):
		new_positions[i] = []
	# print(new_positions)
	for i, drone_comm_params in enumerate(comm_matrix):
		for j, individual_param in enumerate(drone_comm_params):
			if comm_matrix[i,j] == True and len(new_positions[i]) < len(vehicle_names):
				new_positions[i].append(positions[j][1])
	return new_positions


def average_drone_positions(new_positions: list):
	for i, drone_positions in enumerate(new_positions):
		# print('Before the numbers')
		x = 0.0
		y = 0.0
		z = 0.0
		for position in drone_positions:
			# print(position)
			x += position[0]
			y += position[1]
			z += position[2]
		new_positions[i] = [(x/float(len(drone_positions))), (y/float(len(drone_positions))), -abs(z/float(len(drone_positions))), 5]


def fly_to_new_positions(client, vehicle_names: list, new_positions: list, vehicle_offsets: dict, together_tracker: list, stop_matrix: list) -> None:
	for i, name in enumerate(vehicle_names):
		new_position = new_positions[i]
		client.moveToPositionAsync(new_position[0], new_position[1], new_position[2], new_position[3], vehicle_name=name)
		time.sleep(0.1)
		

def transform_to_standard_basis_coordinates(drone_name, new_position: list, vehicle_offsets: list, drone_index) -> list:
	# Each drone reports its position in the x,y,z plane relative to it's own starting position,
	# which is relative to the PlayerStart position in Unreal Engine. You must compensate the reported
	# position of each drone with it's starting position relative to the standard basis to properly calculate
	# the average position for each drone.
	new_position = new_position
	new_position[0] += vehicle_offsets[drone_name][0]
	new_position[1] += vehicle_offsets[drone_name][1]
	new_position[2] += vehicle_offsets[drone_name][2]
	return new_position

def transform_to_relative_basis_coordinates(drone_name, new_position: list, vehicle_offsets: list, drone_index) -> list:
	# Transform the coordinates of each drone their independent representation frames.
	new_position = new_position
	new_position[0] -= vehicle_offsets[drone_name][0]
	new_position[1] -= vehicle_offsets[drone_name][1]
	new_position[2] -= vehicle_offsets[drone_name][2]
	return new_position


def determine_distance_between(vehicle_names: list, position_tracker: list, stop_matrix: list, final_separation_distance: int) -> bool:
	distances = np.zeros((len(vehicle_names), len(vehicle_names)), dtype=float)
	for i, row in enumerate(distances):
		for j, column in enumerate(row):
			if i != j:
				first_drone = position_tracker[i][0]
				second_drone = position_tracker[j][0]
				distances[i, j] = round(haversine(first_drone.latitude, first_drone.longitude, second_drone.latitude, second_drone.longitude)*1000, 3)
			else:
				distances[i, j] = False
	# Produces a boolean matrix, where each position says whether drone i
	# and drone j are within that distance.
	# Check the distances calculated to see whether they are within the
	# separation distance requested.
	together = distances < final_separation_distance
	return together


def update_stop_matrix(together: list):
	for i, row in enumerate(together):
		for j, entry in enumerate(row):
			if i != j and together[i,j] == True:
				stop_matrix[i] = True
				stop_matrix[j] = True


def determine_if_agents_are_together(distance_matrix: list, final_separation_distance: int) -> list:
	"""
	Description: Utilizes the numpy library to determine whether all agents,
				 relative to each other are within the requested separation distance.
	Input: A nxn matrix, where each row contains the distance between the
		   ith agent and jth agent.
	Output: A nxn boolean matrix, where each entry determines whether the ith
			agent and the jth agent are within the separation distance requested.
	"""
	return distance_matrix < final_separation_distance

# ====================================================================================================== #
# Start of main process
# ====================================================================================================== #

# Generate a set of drones based upon a given number input and number of swarms.
# Convention: Capital Letter = Drone Swarm Number = Number of drone in that swarm
# Ex: A1, A2, A3, etc.
# Load list of parameters into the system -> Some sort of class module to set all of these for me.

# Load vehicle names as a list for easy iteration.
# TO DO: This will be drawn from the parameters file loading (Rules sheet)
vehicle_names = ["A", "B", "C"]
vehicle_offsets = {"A": [5, -5, 3], "B": [10, -95, 4], "C": [20, -190, 5]}
time_step = 5 # seconds
final_separation_distance = 10 # meters

# We want a matrix to track who can communicate with who!
# It should be a nxn matrix, with each drone tracking itself and the matrix looks like
#            drone_1 drone_2 ... drone n
# drone_1    true    false   ... true
# drone_2    false   true    ... true
# drone_n    false   false   ... true  
communications_tracker = np.zeros((len(vehicle_names),len(vehicle_names)), dtype=bool)

# We mimic the memory bank of a drone, tracking the relative positions.
# It should be a n-length vector, with each drone tracking itself and the matrix looks like

# drone_1 drone_2 ... drone n
# [x,y,z] [x,y,z] ... [x,y,z]

position_tracker = np.zeros((len(vehicle_names)), dtype=list)

try:
	client = airsim.MultirotorClient()
	print(client)
	client.confirmConnection()
	print('Connection Confirmed')

	enable_control(client, vehicle_names)

	airsim.wait_key('Press any key to takeoff')
	last_vehicle_pointer = takeoff(client, vehicle_names)
	# We wait until the last drone is off the ground
	last_vehicle_pointer.join()

	airsim.wait_key('Press any key to rendevous the drones!')
	start_time = time.time()
	not_together = True
	stop_matrix = np.zeros((len(vehicle_names)), dtype=bool)
	while not_together:
		# Get initial locations
		position_tracker = get_all_drone_positions(client, vehicle_names, position_tracker)
		# print("\n")
		print("="*50)
		for i, row in enumerate(position_tracker):
			print('\n', vehicle_names[i], row)
		
		update_communication_matrix(client, communications_tracker, position_tracker, vehicle_names)

		for i, drone in enumerate(vehicle_names):
			position_tracker[i][1] = transform_to_standard_basis_coordinates(drone, position_to_list(position_tracker[i][1]), vehicle_offsets, i)

		# Propagate location to drones that can communicate
		new_positions = propagate_coordinates(client, communications_tracker, position_tracker, vehicle_names)

		average_drone_positions(new_positions)

		for i, drone in enumerate(vehicle_names):
			new_positions[i] = transform_to_relative_basis_coordinates(drone, new_positions[i], vehicle_offsets, i)

		for i, row in enumerate(new_positions):
			print('\n', vehicle_names[i], row)

		together_tracker = determine_distance_between(vehicle_names, position_tracker, stop_matrix, final_separation_distance)

		update_stop_matrix(together_tracker)

		fly_to_new_positions(client, vehicle_names, new_positions, vehicle_offsets, together_tracker, stop_matrix)
		# Returns a boolean array to track who is together
		time.sleep(time_step)
		print("="*50)
		print('\n')
		if sum(stop_matrix) == len(vehicle_names):
			not_together = False

	end_time = time.time()
	total_time = end_time - start_time
	minutes = round(total_time / 60, 3)
	seconds = (total_time / 60) - minutes
	print("Total Time: {mins} mins {secs} secs".format(mins=minutes, secs=seconds))
	airsim.wait_key('Press any key to reset to original state')
	client.reset()
except Exception:
	traceback.print_exc()
finally:
	if client:
		client.reset()
		disable_control(client, vehicle_names)
	print("Finished!")