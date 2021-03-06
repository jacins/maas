# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""The user handler for the WebSocket connection."""

__all__ = [
    "UserHandler",
]

from django.contrib.auth.models import User
from maasserver.models.user import SYSTEM_USERS
from maasserver.utils.orm import reload_object
from maasserver.websockets.base import (
    Handler,
    HandlerDoesNotExistError,
)


class UserHandler(Handler):

    class Meta:
        queryset = User.objects.filter(is_active=True)
        pk = 'id'
        allowed_methods = ['list', 'get', 'auth_user', 'mark_intro_complete']
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_superuser",
            "sshkeys_count",
        ]
        listen_channels = [
            "user",
        ]

    def get_queryset(self):
        """Return `QuerySet` for users only viewable by `user`."""
        users = super(UserHandler, self).get_queryset()
        if reload_object(self.user).is_superuser:
            # Super users can view all users, except for the built-in users
            return users.exclude(username__in=SYSTEM_USERS)
        else:
            # Standard users can only view their self. We filter by username
            # so a queryset is still returned instead of just a list with
            # only the user in it.
            return users.filter(username=self.user.username)

    def get_object(self, params):
        """Get object by using the `pk` in `params`."""
        obj = super(UserHandler, self).get_object(params)
        if reload_object(self.user).is_superuser:
            # Super user can get any user.
            return obj
        elif obj == self.user:
            # Standard user can only get self.
            return obj
        else:
            raise HandlerDoesNotExistError(params[self._meta.pk])

    def dehydrate(self, obj, data, for_list=False):
        data["sshkeys_count"] = obj.sshkey_set.count()
        return data

    def auth_user(self, params):
        """Return the authenticated user."""
        return self.full_dehydrate(self.user)

    def mark_intro_complete(self, params):
        """Mark the user as completed the intro.

        This is only for the authenticated user. This cannot be performed on
        a different user.
        """
        self.user.userprofile.completed_intro = True
        self.user.userprofile.save()
        return self.full_dehydrate(self.user)
