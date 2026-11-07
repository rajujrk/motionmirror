package com.motionmirror.jobs;

public enum JobKind {
    VALIDATE("validate"), PREPROCESS("preprocess"), PREVIEW("generate"), FULL("generate");

    private final String taskType;

    JobKind(String taskType) {
        this.taskType = taskType;
    }

    public String taskType() {
        return taskType;
    }

    public boolean isGeneration() {
        return this == PREVIEW || this == FULL;
    }
}
