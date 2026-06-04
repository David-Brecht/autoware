# ROS2 / Autoware convenience aliases

alias force_engage='ros2 topic pub /autoware/engage autoware_vehicle_msgs/msg/Engage "{engage: True}" -1'
alias planning_sim='ros2 launch autoware_launch planning_simulator.launch.xml'

merge_compile_commands() {
  find /workspace/build -mindepth 2 -name "compile_commands.json" \
    -print0 | xargs -0 jq -s 'add // []' \
    > /workspace/build/compile_commands.json
  echo "Merged $(find /workspace/build -mindepth 2 -name 'compile_commands.json' | wc -l) compile_commands.json files"
}
