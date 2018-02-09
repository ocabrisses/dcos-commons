package com.mesosphere.sdk.offer.evaluate;

import com.mesosphere.sdk.specification.ResourceSpec;
import org.apache.commons.lang3.builder.ToStringBuilder;

import java.util.Optional;

/**
 * Pairs a {@link ResourceSpec} definition with an existing task's labels associated with that resource.
 */
class ResourceLabels {
    private final ResourceSpec original;
    private final ResourceSpec updated;

    private final String resourceId;
    private final Optional<String> persistenceId;
    private final Optional<String> sourceRoot;

    public ResourceLabels(ResourceSpec resourceSpec, String resourceId) {
        this(resourceSpec, resourceSpec, resourceId);
    }

    public ResourceLabels(ResourceSpec original, ResourceSpec updated, String resourceId) {
        this(original, updated, resourceId, Optional.empty(), Optional.empty());
    }

    public ResourceLabels(
            ResourceSpec original,
            ResourceSpec updated,
            String resourceId,
            Optional<String> persistenceId,
            Optional<String> sourceRoot) {
        this.original = original;
        this.updated = updated;
        this.resourceId = resourceId;
        this.persistenceId = persistenceId;
        this.sourceRoot = sourceRoot;
    }

    public ResourceSpec getOriginal() {
        return original;
    }

    public ResourceSpec getUpdated() {
        return updated;
    }

    public String getResourceId() {
        return resourceId;
    }

    public Optional<String> getPersistenceId() {
        return persistenceId;
    }

    public Optional<String> getSourceRoot() {
        return sourceRoot;
    }

    @Override
    public String toString() {
        return ToStringBuilder.reflectionToString(this);
    }
}
