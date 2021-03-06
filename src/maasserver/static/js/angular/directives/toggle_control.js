/* Copyright 2017 Canonical Ltd.  This software is licensed under the
 * GNU Affero General Public License version 3 (see the file LICENSE).
 *
 * Toggle control.
 */

angular.module('MAAS').directive('toggleCtrl',[
    '$document',
    function($document){

        return {
            restrict: 'A',
            link: function($scope, $element, $attr){

                $scope.isToggled = false;

                $scope.toggleMenu = function() {
                  $scope.isToggled = !$scope.isToggled;
                };

                var clickHandler = function(event) {
                    var isClickedElementChildOfMenu = $element.find(
                                                      event.target).length > 0;
                    if (isClickedElementChildOfMenu) {
                        return;
                    }

                    $scope.$apply(function() {
                        $scope.isToggled = false;
                    });
                };

                $document.on('click', clickHandler);
                $scope.$on('$destroy', function() {
                    $document.off('click', clickHandler);
                });
            }
        };
}]);
