# ROS2 / Autoware convenience aliases

alias force_engage='ros2 topic pub /autoware/engage autoware_vehicle_msgs/msg/Engage "{engage: True}" -1'
alias planning_sim='ros2 launch autoware_launch planning_simulator.launch.xml'
