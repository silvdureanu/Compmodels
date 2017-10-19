import numpy as np
from world import Route, World, Stepper, Turner, NoneCondition, save_route
from net import Willshaw


class Agent(object):
    __latest_agent_id__ = 0

    def __init__(self, init_pos=np.zeros(3), init_rot=np.zeros(2), condition=NoneCondition(), live_sky=True, name=None):
        """

        :param init_pos: the initial position
        :param init_rot: the initial orientation
        :param live_sky: flag to update the sky with respect to the time
        :param name: a name for the agent
        """
        self.pos = init_pos
        self.rot = init_rot
        self.nest = np.zeros(2)
        self.feeder = np.zeros(2)
        self.live_sky = live_sky

        self.homing_routes = []
        self.world = None
        self._net = Willshaw()  # learning_rate=1)
        self.__is_foraging = False
        self.__is_homing = False
        self.dx = 0
        self.condition = condition

        Agent.__latest_agent_id__ += 1
        self.id = Agent.__latest_agent_id__
        if name is None:
            self.name = "agent_%02d" % Agent.__latest_agent_id__
        else:
            self.name = name

    def reset(self):
        """
        Resets the agent at the feeder

        :return: a boolean notifying whether the update of the position and orientation is done or not
        """
        self.__is_foraging = False
        self.__is_homing = True
        self._net.update = False

        if len(self.homing_routes) > 0:
            self.pos[:2] = self.feeder.copy()
            self.rot[1] = self.homing_routes[-1].phi[-1]
            return True
        else:
            # TODO: warn about the existence of the route
            return False

    def add_homing_route(self, rt):
        """
        Updates the homing route, home and nest points.

        :param rt: The route from the feeder to the nest
        :type rt: Route
        :return: a boolean notifying whether the update is done or not
        """
        if not isinstance(rt, Route):
            return False

        if rt not in self.homing_routes:
            rt.condition = self.condition
            self.homing_routes.append(rt)
            self.nest = np.array(rt.xy[-1])
            self.feeder = np.array(rt.xy[0])
            self.dx = rt.dx
            return True
        return False

    def set_world(self, w):
        """
        Update the world of the agent.

        :param w: the world to be placed in
        :return: a boolean notifying whether the update is done or not
        """
        if not isinstance(w, World):
            return False

        self.world = w
        for rt in self.world.routes:
            self.add_homing_route(rt)
        self.world.routes = self.homing_routes
        return True

    def start_learning_walk(self, visualise=None):
        if self.world is None:
            # TODO: warn about not setting the world
            yield None
            return
        elif len(self.homing_routes) == 0:
            # TODO: warn about not setting the homing route
            yield None
            return

        # initialise visualisation
        if visualise in ["top", "panorama"]:
            import pygame

            pygame.init()
            done = False
            if visualise == "top":
                screen = pygame.display.set_mode((1000, 1000))
            elif visualise == "panorama":
                screen = pygame.display.set_mode((1000, 500))

        # let the network update its parameters (learn)
        self._net.update = True

        # learn all the available homing routes
        for r in self.homing_routes:
            # add a copy of the current route to the world to visualise the path
            xs, ys, zs, phis = [self.pos[0]], [self.pos[1]], [self.pos[2]], [self.rot[1]]
            self.world.routes.append(Route(xs, ys, zs, phis, self.condition,
                                           nant=self.id, nroute=len(self.world.routes) + 1))
            counter = 0         # count the steps
            pphi = self.rot[1]  # initialise the last orientation to the current

            for x, y, z, phi in r:
                # stop the loop when we close the visualisation window
                if visualise in ["top", "panorama"]:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            done = True
                    if done:
                        break

                # update the agent position
                self.pos[:] = x, y, z
                self.rot[1] = phi
                # calculate the distance from the start position (feeder)
                dx = np.sqrt(np.square(self.pos[:2] - self.feeder[:2]).sum())
                distance = dx * self.world.ratio2meters

                # update the route in the world
                xs.append(x)
                ys.append(y)
                zs.append(z)
                phis.append(phi)
                self.world.routes[-1] = Route(xs, ys, zs, phis, self.condition, self.id, len(self.world.routes))

                d_phi = np.abs(phi - pphi)
                # TODO: make this parametriseable
                if d_phi > np.pi / 32 or distance // 1 > counter or True:
                    # generate the visual input and transform it to the projecting neurons
                    pn = self.img2pn(self.world_snapshot())
                    # make a forward pass from the network (updating the parameters)
                    en = self._net(pn)
                    counter += 1

                    # update view
                    if visualise == "top":
                        snap, _ = self.world.draw_top_view(width=1000, length=1000)
                    elif visualise == "panorama":
                        snap = self.world_snapshot(width=1000, height=500)
                    if visualise in ["top", "panorama"]:
                        screen.blit(pygame.image.fromstring(snap.tobytes("raw", "RGB"), snap.size, "RGB"), (0, 0))
                        pygame.display.flip()
                        pygame.display.set_caption("% 2d EN: % 2d Distance: %.2f D_phi: % 2.2f" % (
                            counter, en, distance, np.rad2deg(d_phi)))

                        if done:
                            break

                # update last orientation
                pphi = phi

            # remove the copy of the route from the world
            self.world.routes.remove(self.world.routes[-1])
            yield r     # return the learned route

        # freeze the parameters in the network
        self._net.update = False

    def start_homing(self, reset=True, visualise=None):
        if self.world is None:
            # TODO: warn about not setting the world
            return None

        if reset:
            print "Resetting..."
            self.reset()

        # initialise the visualisation
        if visualise in ["top", "panorama"]:
            import pygame

            pygame.init()
            done = False
            if visualise == "top":
                screen = pygame.display.set_mode((1000, 1000))
            elif visualise == "panorama":
                screen = pygame.display.set_mode((1000, 500))

        # add a copy of the current route to the world to visualise the path
        xs, ys, zs, phis = [self.pos[0]], [self.pos[1]], [self.pos[2]], [self.rot[1]]
        self.world.routes.append(Route(xs, ys, zs, phis, condition=self.condition,
                                       nant=self.id, nroute=len(self.world.routes) + 1))
        d_nest = lambda: np.sqrt(np.square(self.pos[:2] - self.nest).sum()) * self.world.ratio2meters
        d_feeder = 0
        counter = 0
        while d_nest() > 0.1:
            x, y, z = self.pos
            phi = self.rot[1]
            # if d_feeder // .1 > counter:
            en = []
            for d_phi in np.linspace(-np.pi / 6, np.pi / 6, 61):
                if visualise in ["top", "panorama"]:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            done = True
                    if done:
                        break

                # generate the visual input and transform to the PN values
                pn = self.img2pn(self.world_snapshot(d_phi=d_phi))
                # make a forward pass from the network
                en.append(self._net(pn))

            if visualise in ["top", "panorama"] and done:
                break

            en = np.array(en).flatten()
            # show preference to the least turning angle
            en += np.append(np.linspace(.01, 0., 30, endpoint=False), np.linspace(0., .01, 31))
            print ("EN:" + " %.2f" * 31 + "\n   " + " %.2f" * 30) % tuple(en)
            phi += np.deg2rad(en.argmin() - 30)

            counter += 1

            self.rot[1] = phi
            self.pos[:] = x + self.dx * np.cos(phi), y + self.dx * np.sin(phi), z
            xs.append(self.pos[0])
            ys.append(self.pos[1])
            zs.append(self.pos[2])
            phis.append(self.rot[1])

            self.world.routes[-1] = Route(xs, ys, zs, phis, condition=self.condition,
                                          nant=self.id, nroute=len(self.world.routes))
            print self.world.routes[-1]

            if visualise == "top":
                snap, _ = self.world.draw_top_view(width=1000, length=1000)
            elif visualise == "panorama":
                snap = self.world_snapshot(width=1000, height=500)
            if visualise in ["top", "panorama"]:
                screen.blit(pygame.image.fromstring(snap.tobytes("raw", "RGB"), snap.size, "RGB"), (0, 0))
                pygame.display.flip()
                pygame.display.set_caption("C: % 2d, EN: % 3d (%.2f), D: %.2f, D_nest: %.2f" % (
                    counter, en.argmin() - 30, en.min(), d_feeder, d_nest()))

            if d_feeder > 15:
                break
            d_feeder += self.dx * self.world.ratio2meters
        self.world.routes.remove(self.world.routes[-1])
        return Route(xs, ys, zs, phis, condition=self.condition, nant=self.id, nroute=len(self.world.routes) + 1)

    def world_snapshot(self, d_phi=0, width=None, height=None):
        x, y, z = (self.pos + .5) * self.world.ratio2meters
        phi = self.rot[1] + d_phi
        img, _ = self.world.draw_panoramic_view(x, y, z, phi, update_sky=self.live_sky,
                                                width=width, length=width, height=height)
        return img

    def img2pn(self, image):
        # TODO: make this parametriseable for different pre-processing of the input
        # keep only the green channel
        return np.array(image).reshape((-1, 3))[:, 1].flatten()


if __name__ == "__main__":
    from world import load_world, load_routes

    update_sky = False
    uniform_sky = False
    condition = Stepper(.1)

    world = load_world()
    world.uniform_sky = uniform_sky
    routes = load_routes()
    routes[0].dx = .1  # 10cm
    world.add_route(routes[0])
    print world.routes[0]

    img, _ = world.draw_top_view(1000, 1000)
    img.save("training-route.png", "PNG")
    # img.show(title="Training route")

    agent = Agent(condition=condition, live_sky=update_sky)
    agent.set_world(world)
    for route in agent.start_learning_walk(visualise="panorama"):
        print "Learned route:", route
        if route is not None:
            save_route(route, "learned-%d-%d" % (route.nant, route.nroute))

    route = agent.start_homing(visualise="top")
    print route
    if route is not None:
        save_route(route, "homing-%d-%d" % (route.nant, route.nroute))

    del world.routes[:]
    world.routes.append(route)
    img, _ = world.draw_top_view(1000, 1000)
    img.save("testing-route.png", "PNG")
    img.show(title="Testing route")
