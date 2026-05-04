# syntax=docker/dockerfile:1
# check=skip=InvalidDefaultArgInFrom
ARG BASE_IMAGE
FROM $BASE_IMAGE

USER root

RUN --mount=type=bind,source=src,target=/tmp/src \
    apt-get update && \
    bash -c 'source /opt/ros/${ROS_DISTRO}/setup.bash && \
             source /opt/autoware/setup.bash && \
             rosdep install -y \
               --from-paths /tmp/src \
               --ignore-src \
               --rosdistro ${ROS_DISTRO} -r'

USER aw
